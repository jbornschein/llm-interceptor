"""
Command-line interface for LLM Interceptor.

Provides the `lli` command with subcommands for watch and config.
"""

from __future__ import annotations

import asyncio
import re
import socket
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import click
from rich.panel import Panel
from rich.table import Table

from lli import __version__
from lli.config import get_cert_info, get_default_trace_dir, load_config
from lli.net import reachable_host_for_listen_host

if TYPE_CHECKING:
    from lli.config import LLIConfig
    from lli.watch import WatchManager

from lli.logger import get_console, setup_logger

# Use the shared console from logger module for coordinated output
# This ensures proper coordination between Live displays and logging
console = get_console()


@click.group()
@click.version_option(version=__version__)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True),
    help="Path to configuration file (TOML or YAML)",
)
@click.pass_context
def main(ctx: click.Context, config_path: str | None) -> None:
    """
    LLM Interceptor (LLI) - Long-running service for LLM traffic analysis.

    A background service that intercepts, analyzes, and logs communications
    between AI coding tools/agents and their backend LLM APIs.
    """
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path




@main.command()
@click.option(
    "--cert-help",
    is_flag=True,
    help="Show certificate installation instructions",
)
@click.option(
    "--proxy-help",
    is_flag=True,
    help="Show proxy configuration instructions",
)
@click.option(
    "--show",
    is_flag=True,
    help="Show current configuration",
)
@click.pass_context
def config(
    ctx: click.Context,
    cert_help: bool,
    proxy_help: bool,
    show: bool,
) -> None:
    """
    Display configuration and setup help.

    Examples:

        lli config --cert-help

        lli config --proxy-help

        lli config --show
    """
    if cert_help:
        _show_cert_help()
    elif proxy_help:
        _show_proxy_help()
    elif show:
        _show_config(ctx.obj.get("config_path"))
    else:
        # Show all help by default
        _show_cert_help()
        console.print()
        _show_proxy_help()



@main.command()
@click.option(
    "--proxy-host",
    "-ph",
    default="127.0.0.1",
    show_default=True,
    help="Proxy server host/interface (use 0.0.0.0 to listen on all interfaces)",
)
@click.option(
    "--proxy-port",
    "-pp",
    "--port",
    "-p",
    type=int,
    default=9090,
    show_default=True,
    help="Proxy server port (default: 9090)",
)
@click.option(
    "--output-dir",
    "--log-dir",
    "-o",
    "output_dir",
    type=click.Path(),
    help="Root output directory (default: ./traces or OS-specific logs dir)",
)
@click.option(
    "--include",
    "-i",
    multiple=True,
    help="Additional URL patterns to include (glob pattern, e.g. '*api.example.com*')",
)
@click.option(
    "--exclude",
    "-x",
    multiple=True,
    help=(
        "URL patterns to exclude (glob). Excluded URLs won't be captured and will also be "
        "bypassed via mitmproxy ignore_hosts (best-effort)."
    ),
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug mode with verbose logging",
)
@click.option(
    "--no-ui",
    is_flag=True,
    default=False,
    help="Disable the web UI server (default: False)",
)
@click.option(
    "--ui-host",
    default="127.0.0.1",
    show_default=True,
    help="Web UI host interface (use 0.0.0.0 to expose on the network)",
)
@click.option(
    "--ui-port",
    default=8000,
    show_default=True,
    type=int,
    help="Web UI port",
)
@click.option(
    "--upstream-ca-cert",
    type=click.Path(exists=False),
    default=None,
    help="Path to PEM or CA bundle for trusting upstream (e.g. corporate proxy) certificates",
)
@click.pass_context
def watch(
    ctx: click.Context,
    proxy_host: str,
    proxy_port: int,
    output_dir: str,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    debug: bool,
    no_ui: bool,
    ui_host: str,
    ui_port: int,
    upstream_ca_cert: str | None,
) -> None:
    """
    Start the long-running service for continuous session capture.

    This starts a background proxy service that automatically manages
    sessions, routing traffic by client identity and message continuity.
    Sessions persist and update in real-time as traffic flows.

    Examples:

        lli watch

        lli watch --proxy-port 8888 --output-dir ./my_traces

        lli watch --proxy-host 0.0.0.0 --proxy-port 9090

        lli watch --ui-host 0.0.0.0 --ui-port 8080

        lli watch --include "*my-custom-api.com*"

        lli watch --exclude "*example.com/health*" --exclude "*example.com/metrics*"

        lli watch --upstream-ca-cert /path/to/corporate-ca.pem

        lli watch --no-ui

    Configure your target application to use this proxy (replace the host/port as needed):

        export HTTP_PROXY=http://127.0.0.1:9090

        export HTTPS_PROXY=http://127.0.0.1:9090

        export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem
    """
    from lli.session_router import SessionRouter
    from lli.exchange_assembler import ExchangeAssembler

    # Load configuration
    config = load_config(ctx.obj.get("config_path"))

    # CLI override for proxy host (explicit --proxy-host overrides config/env)
    if proxy_host != "127.0.0.1":
        config.proxy.host = proxy_host

    # CLI override for proxy port (explicit --proxy-port overrides config/env)
    if proxy_port != 9090:
        config.proxy.port = proxy_port

    # CLI override for upstream CA cert (explicit --upstream-ca-cert overrides config/env)
    if upstream_ca_cert is not None:
        config.proxy.upstream_ca_cert = upstream_ca_cert

    # Add custom glob patterns (user-provided via CLI)
    for pattern in include:
        config.filter.include_globs.append(pattern)

    # Exclude patterns provided via CLI:
    # - Always exclude from capture (filter.exclude_globs)
    # - Also bypass interception via mitmproxy ignore_hosts (proxy.no_proxy)
    #   so these requests don't get MITM'd.
    for pattern in exclude:
        config.filter.exclude_globs.append(pattern)
        config.proxy.no_proxy = config.proxy.no_proxy or []
        config.proxy.no_proxy.append(_glob_to_regex(pattern))

    if debug:
        config.logging.level = "DEBUG"

    # Determine output directory
    if output_dir is None:
        output_dir = str(get_default_trace_dir())

    # Setup logging
    setup_logger(config.logging.level, config.logging.log_file)

    # Check certificate
    cert_info = get_cert_info()
    if not cert_info["exists"]:
        console.print(
            "[yellow]⚠ mitmproxy CA certificate not found.[/]\n"
            "  Run 'lli config --cert-help' for installation instructions.\n"
            "  The certificate will be generated on first run.\n"
        )

    # Create router and assembler
    session_router = SessionRouter(base_dir=Path(output_dir))
    exchange_assembler = ExchangeAssembler()

    # Launch UI server unless disabled
    if not no_ui:
        from lli.server import run_server
        if _is_port_in_use(ui_host, ui_port):
            ui_url = f"http://{reachable_host_for_listen_host(ui_host)}:{ui_port}"
            console.print(Panel(f"Port {ui_port} is already in use.\nAssuming the UI is running at [bold link={ui_url}]{ui_url}[/].\nUse '--no-ui' to silence this message.", title="[bold yellow]Web UI Already Running[/]", border_style="yellow"))
        else:
            server_thread = threading.Thread(target=run_server, args=(Path(output_dir),), kwargs={"host": ui_host, "port": ui_port}, daemon=True)
            server_thread.start()
            ui_url = f"http://{reachable_host_for_listen_host(ui_host)}:{ui_port}"
            console.print(Panel(f"Analyze sessions at: [bold link={ui_url}]{ui_url}[/]", title="[bold green]Web UI Available[/]", border_style="green"))

    # Display startup info
    _display_watch_banner(config.proxy.host, config.proxy.port, output_dir, config)

    stop_event = threading.Event()
    def run_proxy_in_thread() -> None:
        from lli.proxy import run_watch_proxy
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_watch_proxy(config, session_router, exchange_assembler))
        except Exception as e:
            if not stop_event.is_set():
                console.print(f"[red]Proxy error:[/] {e}")
        finally:
            loop.close()

    proxy_thread = threading.Thread(target=run_proxy_in_thread, daemon=True)
    proxy_thread.start()

    try:
        import time
        console.print("[green]Service running. Press Ctrl+C to stop.[/]")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[cyan]Service stopped.[/]")
    finally:
        stop_event.set()
def _show_cert_help() -> None:
    """Display certificate installation instructions."""
    cert_info = get_cert_info()

    console.print("[bold cyan]Certificate Installation Guide[/]")
    console.print("=" * 50)
    console.print()

    console.print(f"[dim]Certificate path:[/] {cert_info['cert_path']}")
    exists_text = (
        "[green]Yes[/]" if cert_info["exists"] else "[yellow]No (will be generated on first run)[/]"
    )
    console.print(f"[dim]Certificate exists:[/] {exists_text}")
    console.print()

    console.print("[bold]macOS:[/]")
    console.print("  1. Run the proxy once to generate the certificate")
    console.print(f"  2. Open: {cert_info['cert_path']}")
    console.print("  3. Double-click to add to Keychain")
    console.print("  4. In Keychain Access, find 'mitmproxy'")
    console.print("  5. Double-click → Trust → 'Always Trust'")
    console.print()

    console.print("[bold]Linux:[/]")
    console.print("  # Ubuntu/Debian:")
    console.print(
        f"  sudo cp {cert_info['cert_path']} /usr/local/share/ca-certificates/mitmproxy.crt"
    )
    console.print("  sudo update-ca-certificates")
    console.print()
    console.print("  # Fedora/RHEL:")
    console.print(f"  sudo cp {cert_info['cert_path']} /etc/pki/ca-trust/source/anchors/")
    console.print("  sudo update-ca-trust")
    console.print()

    console.print("[bold]Windows:[/]")
    console.print(f"  1. Open: {cert_info['cert_path']}")
    console.print("  2. Click 'Install Certificate'")
    console.print("  3. Select 'Local Machine' → Next")
    console.print("  4. 'Place all certificates in the following store'")
    console.print("  5. Browse → 'Trusted Root Certification Authorities'")
    console.print("  6. Finish")


def _show_proxy_help() -> None:
    """Display proxy configuration instructions."""
    config = load_config()
    console.print("[bold cyan]Proxy Configuration Guide[/]")
    console.print("=" * 50)
    console.print()

    console.print("[bold]Environment Variables (Shell):[/]")
    console.print("  export HTTP_PROXY=http://127.0.0.1:9090")
    console.print("  export HTTPS_PROXY=http://127.0.0.1:9090")
    if config.proxy.no_proxy:
        console.print(f"  export NO_PROXY={','.join(config.proxy.no_proxy)}")
    console.print()

    console.print("[bold]Claude Code:[/]")
    console.print("  # Set in your shell before running claude:")
    console.print("  export HTTP_PROXY=http://127.0.0.1:9090")
    console.print("  export HTTPS_PROXY=http://127.0.0.1:9090")
    console.print("  claude")
    console.print()

    console.print("[bold]Cursor IDE:[/]")
    console.print("  # Add to your shell profile (.bashrc, .zshrc):")
    console.print("  export HTTP_PROXY=http://127.0.0.1:9090")
    console.print("  export HTTPS_PROXY=http://127.0.0.1:9090")
    console.print("  # Then restart Cursor from that terminal")
    console.print()

    console.print("[bold]curl:[/]")
    console.print("  curl -x http://127.0.0.1:9090 https://api.anthropic.com/v1/messages ...")
    console.print()

    console.print("[bold]Python requests:[/]")
    console.print("  import requests")
    console.print('  proxies = {"http": "http://127.0.0.1:9090", "https": "http://127.0.0.1:9090"}')
    console.print("  requests.post(url, proxies=proxies, verify=False)")
    console.print()
    console.print("[bold]LAN capture (listen on all interfaces):[/]")
    console.print("  lli watch --lan")
    console.print("  # It will print the detected LAN IP + port for you.")


def _show_config(config_path: str | None) -> None:
    """Display current configuration."""
    config = load_config(config_path)

    console.print("[bold cyan]Current Configuration[/]")
    console.print("=" * 50)
    console.print()

    # Proxy settings
    console.print("[bold]Proxy:[/]")
    console.print(f"  Host: {config.proxy.host}")
    console.print(f"  Port: {config.proxy.port}")
    if config.proxy.no_proxy:
        console.print(f"  No-proxy: {', '.join(config.proxy.no_proxy)}")
    else:
        console.print("  No-proxy: (not set)")
    if config.proxy.upstream_ca_cert:
        console.print(f"  Upstream CA cert: {config.proxy.upstream_ca_cert}")
    else:
        console.print("  Upstream CA cert: (not set)")
    console.print()

    # Filter settings
    console.print("[bold]URL Filters:[/]")
    console.print("  Include patterns:")
    for pattern in config.filter.include_patterns:
        console.print(f"    - {pattern}")
    if config.filter.exclude_patterns:
        console.print("  Exclude patterns:")
        for pattern in config.filter.exclude_patterns:
            console.print(f"    - {pattern}")
    console.print()

    # Storage settings
    console.print("[bold]Storage:[/]")
    console.print(f"  Output file: {config.storage.output_file}")
    console.print(f"  Pretty JSON: {config.storage.pretty_json}")
    console.print()

    # Masking settings
    console.print("[bold]Masking:[/]")
    console.print(f"  Mask auth headers: {config.masking.mask_auth_headers}")
    console.print(f"  Sensitive headers: {', '.join(config.masking.sensitive_headers)}")


@click.option(
    "--proxy-host",
    "-ph",
    default="127.0.0.1",
    show_default=True,
    help="Proxy server host/interface (use 0.0.0.0 to listen on all interfaces)",
)
@click.option(
    "--proxy-port",
    "-pp",
    "--port",
    "-p",
    type=int,
    default=9090,
    show_default=True,
    help="Proxy server port (default: 9090)",
)
@click.option(
    "--output-dir",
    "--log-dir",
    "-o",
    "output_dir",
    type=click.Path(),
    help="Root output directory (default: ./traces or OS-specific logs dir)",
)
@click.option(
    "--include",
    "-i",
    multiple=True,
    help="Additional URL patterns to include (glob pattern, e.g. '*api.example.com*')",
)
@click.option(
    "--exclude",
    "-x",
    multiple=True,
    help=(
        "URL patterns to exclude (glob). Excluded URLs won't be captured and will also be "
        "bypassed via mitmproxy ignore_hosts (best-effort)."
    ),
)
@click.option(
    "--debug",
    is_flag=True,
    help="Enable debug mode with verbose logging",
)
@click.option(
    "--no-ui",
    is_flag=True,
    default=False,
    help="Disable the web UI server (default: False)",
)
@click.option(
    "--ui-host",
    default="127.0.0.1",
    show_default=True,
    help="Web UI host interface (use 0.0.0.0 to expose on the network)",
)
@click.option(
    "--ui-port",
    default=8000,
    show_default=True,
    type=int,
    help="Web UI port",
)
@click.option(
    "--upstream-ca-cert",
    type=click.Path(exists=False),
    default=None,
    help="Path to PEM or CA bundle for trusting upstream (e.g. corporate proxy) certificates",
)
@click.pass_context
def watch(
    ctx: click.Context,
    proxy_host: str,
    proxy_port: int,
    output_dir: str,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    debug: bool,
    no_ui: bool,
    ui_host: str,
    ui_port: int,
    upstream_ca_cert: str | None,
) -> None:
    """
    Start the long-running service for continuous session capture.

    This starts a background proxy service that automatically manages
    sessions, routing traffic by client identity and message continuity.
    Sessions persist and update in real-time as traffic flows.

    Examples:

        lli watch

        lli watch --proxy-port 8888 --output-dir ./my_traces

        lli watch --proxy-host 0.0.0.0 --proxy-port 9090

        lli watch --ui-host 0.0.0.0 --ui-port 8080

        lli watch --include "*my-custom-api.com*"

        lli watch --exclude "*example.com/health*" --exclude "*example.com/metrics*"

        lli watch --upstream-ca-cert /path/to/corporate-ca.pem

        lli watch --no-ui

    Configure your target application to use this proxy (replace the host/port as needed):

        export HTTP_PROXY=http://127.0.0.1:9090

        export HTTPS_PROXY=http://127.0.0.1:9090

        export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem
    """
    from lli.session_router import SessionRouter
    from lli.exchange_assembler import ExchangeAssembler

    # Load configuration
    config = load_config(ctx.obj.get("config_path"))

    # CLI override for proxy host (explicit --proxy-host overrides config/env)
    if proxy_host != "127.0.0.1":
        config.proxy.host = proxy_host

    # CLI override for proxy port (explicit --proxy-port overrides config/env)
    if proxy_port != 9090:
        config.proxy.port = proxy_port

    # CLI override for upstream CA cert (explicit --upstream-ca-cert overrides config/env)
    if upstream_ca_cert is not None:
        config.proxy.upstream_ca_cert = upstream_ca_cert

    # Add custom glob patterns (user-provided via CLI)
    for pattern in include:
        config.filter.include_globs.append(pattern)

    # Exclude patterns provided via CLI:
    # - Always exclude from capture (filter.exclude_globs)
    # - Also bypass interception via mitmproxy ignore_hosts (proxy.no_proxy)
    #   so these requests don't get MITM'd.
    for pattern in exclude:
        config.filter.exclude_globs.append(pattern)
        config.proxy.no_proxy = config.proxy.no_proxy or []
        config.proxy.no_proxy.append(_glob_to_regex(pattern))

    if debug:
        config.logging.level = "DEBUG"

    # Determine output directory
    if output_dir is None:
        output_dir = str(get_default_trace_dir())

    # Setup logging
    setup_logger(config.logging.level, config.logging.log_file)

    # Check certificate
    cert_info = get_cert_info()
    if not cert_info["exists"]:
        console.print(
            "[yellow]⚠ mitmproxy CA certificate not found.[/]\n"
            "  Run 'lli config --cert-help' for installation instructions.\n"
            "  The certificate will be generated on first run.\n"
        )

    # Create router and assembler
    session_router = SessionRouter(base_dir=Path(output_dir))
    exchange_assembler = ExchangeAssembler()

    # Launch UI server unless disabled
    if not no_ui:
        from lli.server import run_server
        if _is_port_in_use(ui_host, ui_port):
            ui_url = f"http://{reachable_host_for_listen_host(ui_host)}:{ui_port}"
            console.print(Panel(f"Port {ui_port} is already in use.\nAssuming the UI is running at [bold link={ui_url}]{ui_url}[/].\nUse '--no-ui' to silence this message.", title="[bold yellow]Web UI Already Running[/]", border_style="yellow"))
        else:
            server_thread = threading.Thread(target=run_server, args=(Path(output_dir),), kwargs={"host": ui_host, "port": ui_port}, daemon=True)
            server_thread.start()
            ui_url = f"http://{reachable_host_for_listen_host(ui_host)}:{ui_port}"
            console.print(Panel(f"Analyze sessions at: [bold link={ui_url}]{ui_url}[/]", title="[bold green]Web UI Available[/]", border_style="green"))

    # Display startup info
    _display_watch_banner(config.proxy.host, config.proxy.port, output_dir, config)

    stop_event = threading.Event()
    def run_proxy_in_thread() -> None:
        from lli.proxy import run_watch_proxy
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_watch_proxy(config, session_router, exchange_assembler))
        except Exception as e:
            if not stop_event.is_set():
                console.print(f"[red]Proxy error:[/] {e}")
        finally:
            loop.close()

    proxy_thread = threading.Thread(target=run_proxy_in_thread, daemon=True)
    proxy_thread.start()

    try:
        import time
        console.print("[green]Service running. Press Ctrl+C to stop.[/]")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[cyan]Service stopped.[/]")
    finally:
        stop_event.set()

def _display_watch_banner(
    proxy_host: str,
    proxy_port: int,
    output_dir: str,
    config: LLIConfig,
) -> None:
    """Display the watch mode startup banner."""
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]LLI Long-Running Service[/]\n[dim]Automatic Session Management[/]",
            border_style="cyan",
        )
    )
    console.print()
    console.print(f"  [cyan]Proxy Host:[/]    {proxy_host}")
    console.print(f"  [cyan]Proxy Port:[/]    {proxy_port}")
    console.print(f"  [cyan]Output Dir:[/]    {output_dir}")
    if config.proxy.upstream_ca_cert:
        ca_path = Path(config.proxy.upstream_ca_cert)
        exists_hint = "[green]exists[/]" if ca_path.exists() else "[yellow]file not found[/]"
        console.print(f"  [cyan]Upstream CA:[/]   {config.proxy.upstream_ca_cert} ({exists_hint})")
    console.print()

    # Display filter rules
    _display_filter_rules(config)

    console.print("[dim]Configure your application:[/]")
    console.print(f"  export HTTP_PROXY=http://{proxy_host}:{proxy_port}")
    console.print(f"  export HTTPS_PROXY=http://{proxy_host}:{proxy_port}")
    if config.proxy.no_proxy:
        console.print(f"  export NO_PROXY={','.join(config.proxy.no_proxy)}")
    console.print("  export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem")
    console.print()


def _display_filter_rules(config: LLIConfig) -> None:
    """Display the current URL filter rules."""
    console.print("[bold cyan]URL Filter Rules:[/]")

    # Built-in include patterns (regex)
    if config.filter.include_patterns:
        console.print("  [green]Include (built-in):[/]")
        for pattern in config.filter.include_patterns:
            # Extract domain name from regex pattern for display
            # e.g., ".*api\.anthropic\.com.*" -> "api.anthropic.com"
            display = pattern.replace(r".*", "").replace("\\.", ".")
            console.print(f"    [dim]•[/] {display}")

    # User-provided include globs
    if config.filter.include_globs:
        console.print("  [green]Include (custom glob):[/]")
        for pattern in config.filter.include_globs:
            console.print(f"    [bold green]•[/] {pattern}")

    # Exclude patterns
    if config.filter.exclude_patterns:
        console.print("  [red]Exclude (regex):[/]")
        for pattern in config.filter.exclude_patterns:
            display = pattern.replace(r".*", "").replace("\\.", ".")
            console.print(f"    [dim]•[/] {display}")

    if config.filter.exclude_globs:
        console.print("  [red]Exclude (custom glob):[/]")
        for pattern in config.filter.exclude_globs:
            console.print(f"    [bold red]•[/] {pattern}")

    console.print()


def _glob_to_regex(glob_pattern: str) -> str:
    """
    Convert a glob-ish URL/host pattern to a regex string.

    Used for mapping `--exclude` patterns to mitmproxy `ignore_hosts`, which expects regex.
    mitmproxy's `ignore_hosts` is evaluated against the request host in most setups,
    so we try to derive a host-oriented regex even if the user provides a full URL glob.
    """
    # Heuristic: prefer matching host portion if we can derive it.
    raw = glob_pattern.strip()
    candidate = raw

    # If it looks like a URL, parse it and use netloc.
    if "://" in raw:
        parsed = urlparse(raw.replace("*", "x").replace("?", "x"))
        if parsed.netloc:
            candidate = parsed.netloc
    else:
        # If it's a URL-ish glob without scheme, drop any path/query fragments.
        candidate = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]

    # Escape regex meta, then replace glob wildcards.
    # Note: we do NOT anchor with ^/$ so substrings match.
    s = re.escape(candidate)
    s = s.replace(r"\*", ".*").replace(r"\?", ".")
    return s



if __name__ == "__main__":
    main(obj={})

def _is_port_in_use(host: str, port: int) -> bool:
    """Return True if the given host:port combination is already bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return True
    return False

