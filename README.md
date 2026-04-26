# LLM Interceptor (LLI) - Long-Running Service Fork

<p align="center">
  <strong>🔍 Proxy-layer microscope for LLM traffic analysis</strong>
</p>

<p align="center">
  A cross-platform command-line tool that intercepts, analyzes, and logs communications between AI coding tools/agents (Claude Code, Cursor, Codex, OpenCode, etc.) and their backend LLM APIs.
</p>

<p align="center">
  <strong>🔥 This fork transforms LLI into a non-interactive, background service with automatic session routing and assembly</strong>
</p>

---

![LLI Web UI](lli-ui-screenshot.png)

## 🚀 What's Different

### Original LLI (by chouzz)
- **CLI tool** with interactive watch mode
- Requires manual `Enter` to start/stop sessions
- Sessions are processed after you manually stop them
- Global log file for all traffic

### This Fork (Long-Running Service)
- **Background service** - runs continuously without user interaction
- **Automatic session routing** - groups requests by client identity and message continuity
- **Eternal sessions** - sessions persist and update in real-time as traffic flows
- **Fork detection** - automatically creates new sessions when conversation branches
- **No manual session management** - sessions are created and updated automatically

## ✨ Features

- **Watch Mode (Background)** - Non-interactive continuous capture with automatic session management
- **Real-Time Exchange Assembly** - Request/response pairs written to disk immediately
- **Session Continuity** - Messages are matched across requests to maintain conversation context
- **Fork Detection** - Automatically creates new session when conversation diverges
- **Transparent Inspection** - See exactly what prompts are sent and what responses are received
- **Streaming Support** - Captures both streaming (SSE) and non-streaming API responses
- **Multi-Provider** - Works with Anthropic, OpenAI, Google, Groq, Together, Mistral, and more
- **Automatic Masking** - Protects API keys and sensitive data in logs
- **Cross-Platform** - Works on Windows, macOS, and Linux

## 📦 Installation

### Using uv (recommended)

```bash
uv tool install llm-interceptor
```

### Using pip

```bash
pip install llm-interceptor
```

### From source (this fork)

```bash
git clone https://github.com/your-username/llm-interceptor.git
cd llm-interceptor
uv sync --dev
uv run lli-dev-setup
```

## 🚀 Quick Start

### 1. Install Certificate (For HTTPS Capture Only)

If you're only capturing HTTP traffic, you can skip this step. Only install the certificate if you need to capture HTTPS requests.

```bash
# Generate certificate
lli watch &
sleep 2
kill %1
```

Then install the certificate:

**macOS:**
```bash
open ~/.mitmproxy/mitmproxy-ca-cert.pem
# Double-click to add to Keychain
# In Keychain Access, find "mitmproxy" → Double-click → Trust → "Always Trust"
```

**Linux (Ubuntu/Debian):**
```bash
sudo cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
sudo update-ca-certificates
```

**Windows:**
Navigate to `%USERPROFILE%\.mitmproxy\`. Double-click `mitmproxy-ca-cert.p12` (or `mitmproxy-ca-cert.cer`) to open the certificate import wizard → Install Certificate → Local Machine → place in **Trusted Root Certification Authorities** → Finish.

### 2. Start the Long-Running Service

```bash
lli watch
```

This starts the proxy service and web UI server in the background. The service:
- Runs continuously until you press `Ctrl+C`
- Automatically creates new sessions as LLM traffic flows
- Updates sessions in real-time as requests and responses arrive
- Writes data to disk immediately (no manual processing needed)

If you need to capture traffic to a **custom or self-hosted API** , use `--include` with a glob pattern, for example:

```bash
lli watch --include "*api.example.com*"
```

### 3. Configure Your Application and Start Dialogue (New Terminal)

```bash
export HTTP_PROXY=http://127.0.0.1:9090
export HTTPS_PROXY=http://127.0.0.1:9090
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem
# Optional: bypass proxy for some hosts (e.g. localhost). Configure in lli.toml as no_proxy or run: lli config --proxy-help

# Run Claude and start your conversation
claude
# Now start your dialogue - all prompts and responses will be automatically captured into sessions
```

### 4. (Optional) Corporate network: upstream CA certificate

If you capture traffic **behind a corporate proxy** or on a network where upstream servers use a **company-signed certificate**, you may see TLS errors because the proxy only trusts the system CAs, not your company's CA. Configure the **upstream** trust CA so LLI (mitmproxy) can verify connections to the corporate proxy or target hosts:

- **Client → LLI:** Your app must trust the **mitmproxy CA** (install `~/.mitmproxy/mitmproxy-ca-cert.pem` as in step 1).
- **LLI → upstream (corporate proxy / target):** LLI must trust the **company CA**. Set the path to your company's root or intermediate CA (PEM file):

```bash
# Option A: CLI
lli watch --upstream-ca-cert /path/to/corporate-ca.pem

```

Use `lli config --show` to confirm the upstream CA path. If the file does not exist at startup, LLI will exit with an error.

### 5. Visualize with Web UI

The web interface should be launched in http://127.0.0.1:8000 to analyze captured conversations:

In the UI, you can:
- Browse captured sessions in the sidebar
- View conversation flow between requests and responses
- Inspect detailed API payloads and metadata
- Search and filter through captured data
- Copy formatted content for further analysis
- Sessions update automatically in real-time as traffic flows

## 🎬 How the Long-Running Service Works

The service operates continuously, automatically managing sessions:

| Event | Action |
|-------|--------|
| First request for a new conversation | Creates a new session directory |
| Subsequent requests with same session ID | Routes to existing session |
| Request with different system prompt | Creates forked session |
| Request with older message history | Creates forked session |
| Request/response pair complete | Written to disk immediately |

### Example Session Flow

```
$ lli watch

Service running. Press Ctrl+C to stop.

# Session 1 starts automatically when first LLM request arrives
[INFO] Created new session session_20260426_120000

# Session 2 created when conversation forks (different system prompt)
[INFO] Created new session session_20260426_121500 (forked from session_20260426_120000)

# Requests continue to stream in and out
[INFO] Routed request to existing session session_20260426_120000
[INFO] Captured request to https://api.anthropic.com/v1/messages (Session: session_20260426_120000)

# All data is written to disk in real-time
# Session directories grow as traffic flows
```

### Output Structure

```text
./traces/                                    # Root output directory
├── session_20260426_120000/                 # Session folder (auto-created)
│   ├── session_meta.json                    # Session metadata (ID, timestamp, client info)
│   ├── annotations.json                     # User annotations (notes)
│   ├── 00001_request_2026-04-26_12-00-00.json
│   ├── 00001_response_2026-04-26_12-00-02.json
│   ├── 00002_request_2026-04-26_12-00-05.json
│   ├── 00002_response_2026-04-26_12-00-07.json
│   └── ...
│
├── session_20260426_121500/                 # Forked session (different conversation branch)
│   ├── session_meta.json
│   ├── 00001_request_2026-04-26_12-15-00.json
│   └── ...
│
└── ...
```

## 📋 CLI Reference

### `lli watch`

Start the long-running proxy service (non-interactive, continuous capture).

```bash
lli watch [OPTIONS]

Options:
  -p, --port INTEGER           Proxy server port (default: 9090)
  -o, --output-dir, --log-dir PATH  Root output directory (default: ./traces or OS log dir)
  -i, --include TEXT           Additional URL patterns to include (glob pattern)
  --upstream-ca-cert PATH      Path to PEM or CA bundle for trusting upstream (e.g. corporate proxy) certificates
  --no-ui                      Disable web UI server
  --ui-host TEXT               Web UI host (default: 127.0.0.1)
  --ui-port INTEGER            Web UI port (default: 8000)
  --debug                      Enable debug mode with verbose logging
```

**Examples:**

```bash
# Basic long-running service
lli watch

# Custom port and output directory
lli watch --port 8888 --output-dir ./my_traces

# Include custom API endpoint (glob pattern)
lli watch --include "*my-custom-api.com*"

# Corporate network: trust company CA so upstream TLS (proxy/target) is verified
lli watch --upstream-ca-cert /path/to/corporate-ca.pem

# Disable web UI (use only proxy)
lli watch --no-ui
```

### `lli config`

Display configuration and setup help.

```bash
lli config --cert-help    # Certificate installation instructions
lli config --proxy-help   # Proxy configuration instructions
lli config --show         # Show current configuration
```

## 🔧 Supported LLM Providers

LLI is pre-configured to capture traffic from:

| Provider | API Domain |
|----------|------------|
| Anthropic | `api.anthropic.com` |
| OpenAI | `api.openai.com` |
| Google | `generativelanguage.googleapis.com` |
| Together | `api.together.xyz` |
| Groq | `api.groq.com` |
| Mistral | `api.mistral.ai` |
| Cohere | `api.cohere.ai` |
| DeepSeek | `api.deepseek.com` |

Add custom providers with `--include` (using glob patterns):
```bash
lli watch --include "*my-custom-api.com*"
```

## 🐛 Troubleshooting

### SSL Certificate Error

**Problem:** `SSL: CERTIFICATE_VERIFY_FAILED`

**Solution:** Install the mitmproxy CA certificate. Run `lli config --cert-help` for instructions.

### Node.js Apps Not Working

**Problem:** Requests hang or timeout when using Claude Code, Cursor, etc.

**Solution:** Set the `NODE_EXTRA_CA_CERTS` environment variable:
```bash
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem
```

### TLS / handshake errors behind corporate proxy

**Problem:** Upstream TLS handshake failures when capturing company URLs (e.g. traffic goes through a corporate proxy that uses a company CA).

**Solution:** Configure the upstream trust CA so LLI can verify the corporate proxy or target server certificate. Use `--upstream-ca-cert`, or set `proxy.upstream_ca_cert` in `lli.toml`, or `LLI_UPSTREAM_CA_CERT`. See the "Corporate network: upstream CA certificate" section above.

### No Traffic Captured

**Problem:** Watch mode is running but no requests are logged

**Solution:**
1. Verify proxy environment variables are set correctly
2. Make sure the URL matches the default patterns (or add `--include`)
3. Check `lli config --show` to see current filter patterns

## 📜 License

MIT License

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
Before committing from a fresh clone, run `lli-dev-setup` once to install the
repository's `pre-commit` hook locally.

## 📞 Support

- GitHub Issues: [Report a bug](https://github.com/chouzz/llm-interceptor/issues)
- Documentation: [Read the docs](https://github.com/chouzz/llm-interceptor#readme)

---

**Note:** This is a fork of the original [LLM Interceptor](https://github.com/chouzz/llm-interceptor) project by @chouzz, transformed into a long-running background service. The original project remains active and may have different features or architecture.
