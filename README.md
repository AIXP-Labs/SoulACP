# soulacp

[![CI](https://github.com/AIXP-Labs/SoulACP/actions/workflows/ci.yml/badge.svg)](https://github.com/AIXP-Labs/SoulACP/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/soulacp)](https://pypi.org/project/soulacp/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

Python ACP client library — connect to AI coding agents with zero session overhead.

## Features

- **29 CLI adapters**: Claude Code, Gemini, OpenCode, OpenClaw, Cursor, Codex, Qwen, Kimi, Codebuddy, Cline, Copilot, Minion, Vibe, Nova, Crow, Amp, Auggie, Autohand, Corust, DeepAgents, Factory Droid, fast-agent, Copilot LS, Goose, Junie, Kilo, pi ACP, Qoder, Stakpak
- **No own session layer**: Directly manages CLI's native session
- **Zero token overhead**: No middleware, no duplicate session history
- **ManagedSession**: High-level API with auto session reuse, rotation, retry, fallback
- **Connection pooling**: Reuse idle connections, session matching
- **Session store**: Persistent user→session mapping with TTL (Memory or File cache)
- **Async streaming**: Real-time response chunks via asyncio
- **Auto-retry**: Exponential backoff with jitter
- **Host services**: Built-in file system and terminal services for CLI tool calls
- **Pure stdlib**: No external dependencies

## Install

```bash
pip install soulacp
```

## Quick Start

### ManagedSession (recommended)

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="claude", model="claude-sonnet-4-20250514") as session:
        # Simple query — session auto-managed
        response = await session.query("Hello!", user_id="user1")
        print(response)

        # Streaming
        async for chunk in session.stream("Write hello world", user_id="user1"):
            print(chunk, end="", flush=True)

asyncio.run(main())
```

ManagedSession automatically handles:
- **Session reuse**: Same user_id gets same CLI session across requests
- **Session rotation**: Fresh session on "prompt too long" overflow
- **Retry**: Exponential backoff on transient errors
- **Fallback**: Switch to alternate provider on persistent failure

### Low-level API

```python
import asyncio
from soulacp import ACPConfig, ACPConnectionPool, resolve_client_class

async def main():
    config = ACPConfig(provider="claude", model="claude-sonnet-4-20250514")
    client_class = resolve_client_class("claude")
    pool = ACPConnectionPool(config, client_class)

    async with pool.acquire() as (client, session_id):
        response = await client.query("Hello!")
        print(response)

    await pool.close_all()

asyncio.run(main())
```

## Supported Agents

| Agent | Provider | Model Example | CLI Command |
|-------|----------|---------------|-------------|
| Claude Code | `claude` | `claude-sonnet-4-20250514` | `claude` or `claude-code-acp` |
| Gemini | `gemini` | `gemini-3-flash-preview` | `gemini --acp` |
| OpenCode | `opencode` | — | `opencode acp` |
| OpenClaw | `openclaw` | — | `openclaw acp` |
| Cursor (ACP) | `cursor` | `cursor-acp/default` | `cursor-agent acp` |
| Cursor (Legacy) | `cursor-cli` | `cursor-cli/gpt-4` | `cursor-agent -p` |
| Codex | `codex` | `codex-acp/gpt-5` | `codex-acp` |
| Qwen Code | `qwen` | `qwen-acp/qwen-coder` | `qwen-code --acp --experimental-skills` |
| Kimi CLI | `kimi` | `kimi-acp/default` | `kimi acp` |
| Codebuddy Code | `codebuddy` | `codebuddy-acp/default` | `codebuddy-code --acp` |
| Cline | `cline` | `cline-acp/default` | `cline --acp` |
| GitHub Copilot | `copilot` | `copilot-acp/default` | `copilot --acp` |
| Minion Code | `minion` | `minion-acp/default` | `minion-code acp` |
| Mistral Vibe | `vibe` | `vibe-acp/default` | `vibe-acp` |
| Nova | `nova` | `nova-acp/default` | `nova acp` |
| Crow CLI | `crow` | `crow-acp/default` | `crow-cli acp` |
| Amp | `amp` | `amp-acp/default` | `amp-acp` |
| Auggie | `auggie` | `auggie-acp/default` | `auggie --acp` |
| Autohand | `autohand` | `autohand-acp/default` | `autohand-acp` |
| Corust Agent | `corust` | `corust-acp/default` | `corust-agent-acp` |
| DeepAgents | `deepagents` | `deepagents-acp/default` | `deepagents-acp` |
| Factory Droid | `droid` | `droid-acp/default` | `droid exec --output-format acp` |
| fast-agent | `fastagent` | `fastagent-acp/default` | `fast-agent-acp -x` |
| Copilot LS | `copilot-ls` | `copilot-ls-acp/default` | `copilot-language-server --acp` |
| Goose | `goose` | `goose-acp/default` | `goose acp` |
| Junie | `junie` | `junie-acp/default` | `junie --acp=true` |
| Kilo | `kilo` | `kilo-acp/default` | `kilo acp` |
| pi ACP | `pi` | `pi-acp/default` | `pi-acp` |
| Qoder | `qoder` | `qoder-acp/default` | `qodercli --acp` |
| Stakpak | `stakpak` | `stakpak-acp/default` | `stakpak acp` |

## Usage

### Claude Code

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="claude", model="claude-sonnet-4-20250514") as session:
        response = await session.query("Hello!")
        print(response)

asyncio.run(main())
```

### Gemini

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="gemini", model="gemini-3-flash-preview") as session:
        response = await session.query("Hello!")
        print(response)

asyncio.run(main())
```

### Codex (OpenAI)

Codex uses ChatGPT login (no `OPENAI_API_KEY` required) — run `codex login` once,
then soulacp picks up `~/.codex/auth.json` automatically.

```python
import asyncio
from soulacp import ManagedSession

async def main():
    # gpt-5.5 is the ChatGPT-auth friendly model (recommended for ManagedSession)
    async with ManagedSession(provider="codex", model="codex-acp/gpt-5.5") as session:
        response = await session.query("Hello!")
        print(response)

asyncio.run(main())
```

**Environment variables** (soulacp 0.1.5+, all optional):

| Variable | Values | Default | Purpose |
|----------|--------|---------|---------|
| `CODEX_REASONING_EFFORT` | `minimal` / `low` / `medium` / `high` / `xhigh` | `medium` | Reasoning budget |
| `CODEX_SANDBOX_MODE` | `read-only` / `workspace-write` / `danger-full-access` | (codex default) | File-system permission |
| `CODEX_APPROVAL_POLICY` | `untrusted` / `on-failure` / `on-request` / `never` | (codex default) | Approval prompts |
| `CODEX_NETWORK_ACCESS` | `true` / `false` | `false` | Allow network in `workspace-write` |

To match Claude's "full autonomy" mode (no prompts, can write within workspace, network on):

```bash
export CODEX_SANDBOX_MODE=workspace-write
export CODEX_APPROVAL_POLICY=never
export CODEX_NETWORK_ACCESS=true
```

For API-key auth instead of ChatGPT, set `OPENAI_API_KEY` and pick an API-keyed model
(e.g. `codex-acp/gpt-5.5-codex`, `codex-acp/o3`, `codex-acp/o4-mini`).

### Multi-Agent

```python
import asyncio
from soulacp import ManagedSession

async def main():
    # Claude for code generation
    async with ManagedSession(provider="claude", model="claude-sonnet-4-20250514") as claude:
        code = await claude.query("Write a sorting algorithm in Python")

    # Gemini for code review
    async with ManagedSession(provider="gemini", model="gemini-3-flash-preview") as gemini:
        review = await gemini.query(f"Review this code:\n{code}")

asyncio.run(main())
```

## Session Management

### ManagedSession + ProviderSessionStore

ManagedSession uses ProviderSessionStore internally to map `(user_id, provider)` to CLI session IDs:

```python
from soulacp import ManagedSession

session = ManagedSession(provider="claude", model="claude-sonnet-4-20250514")

# First request — creates new CLI session, stores mapping
await session.query("Remember 42.", user_id="alice")

# Second request — reuses same CLI session (alice→session_abc)
await session.query("What number?", user_id="alice")

# Different user — gets different CLI session
await session.query("Hello!", user_id="bob")
```

### Custom Session Store

```python
from soulacp import ManagedSession, ProviderSessionStore, FileCache

# Persistent file-based session store
store = ProviderSessionStore(cache=FileCache("~/.soulacp/sessions.json"))
session = ManagedSession(provider="claude", model="claude-sonnet-4-20250514", session_store=store)
```

### Session Lifecycle

| Event | Behavior |
|-------|----------|
| First request | New CLI session created, mapping stored (TTL 7 days) |
| Subsequent requests | Same user_id → same CLI session (reuse) |
| "Prompt too long" | Auto-rotate to fresh session, clear old mapping |
| Connection error | Clear mapping, retry with new session |
| Fallback | Switch to alternate provider (e.g. claude→gemini) |

### Cache Backends

```python
from soulacp import MemoryCache, FileCache

# In-memory (default) — fast, lost on restart
memory = MemoryCache(max_size=10000)

# File-based — persists across restarts
file_cache = FileCache("~/.soulacp/sessions.json", debounce_seconds=1.0)
```

### Low-level Session Control

```python
from soulacp import ACPConnectionPool, ACPConfig, resolve_client_class

config = ACPConfig(provider="claude", model="claude-sonnet-4-20250514")
pool = ACPConnectionPool(config, resolve_client_class("claude"))

# Explicit session reuse
async with pool.acquire() as (client, sid):
    await client.query("Remember 42.")

async with pool.acquire(session_id=sid) as (client, sid2):
    assert sid2 == sid  # Same CLI session
    response = await client.query("What number?")
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ACP_PROVIDER` | — | Provider name |
| `ACP_MODEL` | — | Model identifier |
| `ACP_POOL_SIZE` | 10 | Max pool connections |
| `ACP_TIMEOUT_CONNECT` | 30 | Connection timeout (seconds) |
| `ACP_TIMEOUT_PROMPT` | 2592000 | Prompt timeout (seconds, 30 days) |
| `ACP_TIMEOUT_STREAM` | 2592000 | Stream chunk timeout (seconds, 30 days) |
| `ACP_POOL_IDLE_TIMEOUT` | 2592000 | Pool idle timeout (seconds, 30 days) |
| `ACP_AUTO_APPROVE` | true | Auto-approve tool permissions |
| `ACP_MAX_RETRIES` | 3 | Max retry attempts |

### Programmatic

```python
from soulacp import ACPConfig

config = ACPConfig(
    provider="claude",
    model="claude-sonnet-4-20250514",
    pool_size=5,
    timeout_connect=60,
    auto_approve_permissions=True,
    enable_fallback=True,
)
```

### From Environment

```python
from soulacp import ACPConfig

config = ACPConfig.from_env()
```

## Architecture

```
ManagedSession (high-level API)
  ├── ProviderSessionStore (user→session mapping)
  │   └── CacheBackend (MemoryCache / FileCache)
  ├── ACPConnectionPool (connection reuse + health check)
  │   └── ACPClientBase (JSON-RPC over stdio subprocess)
  │       ├── ClaudeACPClient      ├── ClineACPClient
  │       ├── GeminiACPClient      ├── CopilotACPClient
  │       ├── OpenCodeACPClient    ├── MinionACPClient
  │       ├── OpenClawACPClient    ├── VibeACPClient
  │       ├── CursorACPClient      ├── NovaACPClient
  │       ├── CodexACPClient       ├── CrowACPClient
  │       ├── QwenACPClient        └── CursorCLIClient (legacy)
  │       ├── KimiACPClient
  │       └── CodebuddyACPClient
  └── Services
      ├── FSService (file system operations)
      └── TerminalService (subprocess execution)
```

## Testing

```bash
# Unit tests (no CLI required)
pytest tests/ --ignore=tests/test_integration*.py -v

# Integration tests (requires specific CLI installed, auto-skips if not available)
pytest tests/test_integration.py -v          # Claude Code
pytest tests/test_integration_gemini.py -v   # Gemini
pytest tests/test_integration_codex.py -v    # Codex

# All tests
pytest tests/ -v
```

## AIXP Labs [aixp.dev](https://aixp.dev)

AIXP Labs develops and maintains the following core projects:

| Project | Description | Website |
|---------|-------------|---------|
| [HSAW](https://hsaw.dev) | Human Sovereignty and Wellbeing — Axiom 0 white paper (foundation) | hsaw.dev |
| [AILP](https://ailp.dev) | AI List Protocol — agent discovery and capability advertising | ailp.dev |
| [AIVP](https://aivp.dev) | AI Value Protocol — international commerce, crypto asset settlement | aivp.dev |
| [AIRP](https://airp.dev) | AI RMB Protocol — Mainland China commerce, RMB licensed settlement | airp.dev |
| [AIBP](https://aibp.dev) | AI Bot Protocol — social communication and trust | aibp.dev |
| [AIAP](https://aiap.dev) | AI Application Protocol — governance and compliance | aiap.dev |
| [AISOP](https://aisop.dev) | AI Standard Operating Protocol — flow program definition | aisop.dev |
| [SoulBot](https://soulbot.dev) | AI agent runtime and framework | soulbot.dev |
| [SoulACP](https://soulacp.dev) | Adapter library — bridging CLI tools and LLM providers (this project) | soulacp.dev |

## ⚠️ Disclaimer

This software is **experimental** and provided for **research and educational purposes only**. Not intended for production use. Use at your own risk. The authors assume no liability for any damages arising from the use of this software. See [LICENSE](LICENSE) for full terms (Apache 2.0).

## License

[Apache License 2.0](LICENSE) - Copyright 2026 AIXP Labs AIXP.dev | SoulACP.dev

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.1.2. www.soulacp.dev
