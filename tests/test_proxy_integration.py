"""Minimal integration tests for proxy flow (mitmproxy API usage)."""

import json
from pathlib import Path

from lli.config import LLIConfig, FilterConfig
from lli.filters import URLFilter
from lli.session_router import SessionRouter
from lli.exchange_assembler import ExchangeAssembler
from lli.proxy import WatchAddon


def test_proxy_addon_initialization(tmp_path: Path) -> None:
    """Test that WatchAddon initializes correctly with all dependencies."""
    config = LLIConfig()
    session_router = SessionRouter(base_dir=tmp_path)
    exchange_assembler = ExchangeAssembler()
    url_filter = URLFilter(config.filter)

    # Should not raise any errors
    addon = WatchAddon(
        config=config,
        session_router=session_router,
        exchange_assembler=exchange_assembler,
        url_filter=url_filter,
    )

    assert addon.config == config
    assert addon.session_router == session_router
    assert addon.exchange_assembler == exchange_assembler
    assert addon.url_filter == url_filter


def test_proxy_addon_routes_request_to_session(tmp_path: Path) -> None:
    """Test that a request is routed to a session and written to disk."""
    config = LLIConfig()
    session_router = SessionRouter(base_dir=tmp_path)
    exchange_assembler = ExchangeAssembler()
    url_filter = URLFilter(config.filter)
    addon = WatchAddon(
        config=config,
        session_router=session_router,
        exchange_assembler=exchange_assembler,
        url_filter=url_filter,
    )

    # Create a mock flow
    class MockRequest:
        def __init__(self):
            self.pretty_url = "https://api.anthropic.com/v1/messages"
            self.method = "POST"
            self.headers = {
                "content-type": "application/json",
                "x-api-key": "sk-12345",  # Should be masked
            }
            self.content = json.dumps({
                "model": "claude-3-sonnet",
                "messages": [{"role": "user", "content": "Hello"}],
            }).encode()

    class MockFlow:
        def __init__(self):
            self.request = MockRequest()

    flow = MockFlow()

    # Call request handler
    addon.request(flow)

    # Verify session was created and request was written
    session_dirs = list(tmp_path.glob("session_*"))
    assert len(session_dirs) == 1

    session_dir = session_dirs[0]
    files = list(session_dir.glob("*.json"))
    # Should have request file + session_meta.json
    assert len(files) == 2
    request_files = [f for f in files if "request" in f.name]
    assert len(request_files) == 1
    assert files[0].name.startswith("00001_request_")

    # Verify the request content
    content = json.loads(files[0].read_text())
    assert content["url"] == "https://api.anthropic.com/v1/messages"
    assert content["method"] == "POST"
    # API key should be masked (partial masking shown)
    assert "sk-1234" in content["headers"]["x-api-key"]
    assert content["headers"]["x-api-key"].endswith("***")


def test_proxy_addon_routes_request_based_on_url_filter(tmp_path: Path) -> None:
    """Test that requests are filtered by URL before being captured."""
    config = LLIConfig()
    session_router = SessionRouter(base_dir=tmp_path)
    exchange_assembler = ExchangeAssembler()
    url_filter = URLFilter(config.filter)
    addon = WatchAddon(
        config=config,
        session_router=session_router,
        exchange_assembler=exchange_assembler,
        url_filter=url_filter,
    )

    # Create a mock flow for a non-matching URL
    class MockRequest:
        def __init__(self):
            self.pretty_url = "https://example.com/api"
            self.method = "POST"
            self.headers = {"content-type": "application/json"}
            self.content = b'{}'

    class MockFlow:
        def __init__(self):
            self.request = MockRequest()

    flow = MockFlow()

    # Call request handler - should skip non-matching URL
    addon.request(flow)

    # Verify no session was created
    session_dirs = list(tmp_path.glob("session_*"))
    assert len(session_dirs) == 0
