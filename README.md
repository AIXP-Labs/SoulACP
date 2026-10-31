# SoulACP

**Python client library for the Agent Client Protocol, with native CLI sessions and bounded connection pooling.**

[![CI](https://github.com/AIXP-Labs/SoulACP/actions/workflows/ci.yml/badge.svg)](https://github.com/AIXP-Labs/SoulACP/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/soulacp)](https://pypi.org/project/soulacp/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

English | [简体中文](README_CN.md)

## Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Supported Agents](#supported-agents)
- [Usage](#usage)
- [Session Management](#session-management)
- [Configuration](#configuration)
- [Privacy Boundaries](#privacy-boundaries)
- [Error Handling](#error-handling)
- [Architecture](#architecture)
- [Testing](#testing)
- [Project Documents](#project-documents)
- [AIXP Labs](#aixp-labs-aixpdev)
- [License](#license)
- [Alignment & Philosophy](#alignment-philosophy)

## Features

- **30 CLI integrations**: 29 ACP adapters plus one legacy Cursor CLI adapter; see [Supported Agents](#supported-agents)
- **Native CLI sessions**: The provider CLI remains the source of truth; soulacp stores only session mappings and transport state
- **No duplicated conversation history**: Prompts are not replayed through a second middleware-owned transcript
- **ManagedSession**: High-level API with safe session reuse, explicit reset, startup retry, and fallback
- **Bounded connection pooling**: Session-safe reuse, FIFO backpressure, lifecycle retirement
- **Session store**: TTL-backed user→session mapping with memory or file storage
- **Async streaming**: Real-time response chunks via asyncio
- **Safe retry**: Exponential backoff for retryable control-plane failures; prompts are never blindly replayed
- **Structured session context**: Validated additional roots and stdio/HTTP/SSE MCP server definitions
- **Host services**: Configured-root file access and bounded ACP terminal process management
- **Current ACP lifecycle**: Capability-gated authentication, logout, session close, request cancellation, and config APIs
- **Pure stdlib core**: No required runtime dependencies; the OpenTelemetry API is optional

## Installation

SoulACP requires Python 3.10 or later and at least one supported agent CLI or
ACP bridge installed and, when required, authenticated.

### Package-index release

```bash
pip install soulacp
```

GitHub and PyPI releases use the same version and are published together.
Use `python -m pip install soulacp` to select a Python interpreter explicitly.

### Current source

You can also clone the GitHub repository and install from source locally.
To install a particular release, check out its matching release tag first.

```bash
git clone https://github.com/AIXP-Labs/SoulACP.git
cd SoulACP
python -m pip install -e .

# Development and test tools
python -m pip install -e ".[dev]"
```

Repository hooks are installed separately with Python >=3.11; library and test
support remains >=3.10. See [the hook environment](CONTRIBUTING.md#repository-hook-environment).

Check the interpreter, distribution metadata, and imported module together:

```bash
python -c "import importlib.metadata as m, soulacp; print(m.version('soulacp')); print(soulacp.__version__)"
```

Both values should be `0.2.1` when validating this checkout. Locally inspect
`soulacp.__file__` if the values differ; redact personal paths before sharing
diagnostics. Editable installs, wheels, and downstream lock files are distinct
installation sources. Replacing files does not reload an already-running worker.

### Optional telemetry

```bash
# Published package
python -m pip install "soulacp[otel]"

# Current source checkout
python -m pip install -e ".[otel]"
```

## Quick Start

### ManagedSession (recommended)

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="claude", model="claude-acp/sonnet") as session:
        # Simple query — session auto-managed
        response = await session.query("Hello!", user_id="user1")
        print(response)

        # Streaming
        async for chunk in session.stream("Write hello world", user_id="user1"):
            print(chunk, end="", flush=True)

asyncio.run(main())
```

ManagedSession automatically handles:

- **Session reuse**: Reuse the user's logical session while its mapping remains valid and the provider supports restoration
- **Explicit reset**: `reset_session(user_id)` clears a reviewed stale/overflowed session without implicit prompt replay
- **Retry**: Exponential backoff on safe, pre-prompt transient failures
- **Fallback**: Optional provider switch only when the prompt outcome is known not to be in progress

### Low-level API

```python
import asyncio
from soulacp import ACPConfig, ACPConnectionPool, resolve_client_class

async def main():
    config = ACPConfig(provider="claude", model="claude-acp/sonnet")
    client_class = resolve_client_class("claude")

    async with ACPConnectionPool(config, client_class) as pool:
        async with pool.acquire() as (client, session_id):
            response = await client.query("Hello!")
            print(response)

asyncio.run(main())
```

## Supported Agents

| Agent | Provider | Model Example | Base Command |
|-------|----------|---------------|-------------|
| Claude Code | `claude` | `claude-acp/sonnet` | `claude-agent-acp` (`claude-code-acp` legacy) |
| Gemini | `gemini` | `gemini-acp/gemini-3-flash-preview` | `gemini --acp` |
| OpenCode | `opencode` | `opencode-acp/default` | `opencode acp` |
| OpenClaw | `openclaw` | `openclaw/default` | `openclaw acp --no-prefix-cwd` |
| Cursor (ACP) | `cursor` | `cursor-acp/default` | `cursor-agent acp` |
| Cursor (Legacy) | `cursor-cli` | `cursor-cli/gpt-4` | `cursor-agent -p` |
| Codex | `codex` | `codex-acp/gpt-5.5` | `codex-acp` |
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
| Factory Droid | `droid` | `droid-acp/default` | `droid exec --output-format acp-daemon` |
| fast-agent | `fastagent` | `fastagent-acp/default` | `fast-agent-acp -x` |
| Copilot LS | `copilot-ls` | `copilot-ls/default` | `copilot-language-server --acp` |
| Goose | `goose` | `goose-acp/default` | `goose acp` |
| Junie | `junie` | `junie-acp/default` | `junie --acp=true` |
| Kilo | `kilo` | `kilo-acp/default` | `kilo acp` |
| pi ACP | `pi` | `pi-acp/default` | `pi-acp` |
| Qoder | `qoder` | `qoder-acp/default` | `qodercli --acp` |
| Stakpak | `stakpak` | `stakpak-acp/default` | `stakpak acp` |

The base-command column is not an installation command. Adapters can append
model, policy, or authentication arguments at launch. Model availability
depends on the installed CLI, account, and authentication method.
The namespaced model ID `provider-acp/default` (and `provider/default`) leaves
model selection to the provider; SoulACP does not send `default` as a literal
model name or overwrite an existing provider model setting.

Registry helpers expose best-effort installation hints and inspect `PATH`
without starting provider processes:

```python
from soulacp import list_agents, list_installed_agents

for agent in list_agents():
    print(f"{agent.name:12} {agent.install_cmd}")

installed = [agent.name for agent in list_installed_agents()]
print("Installed:", installed)
```

Install the current Claude and Codex ACP bridges with:

```bash
npm install -g "@agentclientprotocol/claude-agent-acp@>=0.86.0" "@agentclientprotocol/codex-acp@>=2.1.1"
```

Keep the quotes so the shell does not treat `>` as a redirect. These ranges
match SoulACP 0.2.1's installation hints; they are not exact version pins or
qualification of every later bridge/model combination. A bridge can bundle its
own CLI/runtime, so upgrading a global `claude` or `codex` alone does not prove
which runtime the bridge will use. Check the bridge's installed package metadata
and its reported `agent_info` without copying credentials or full session state.

The renamed Claude bridge is preferred; `claude-code-acp` remains a legacy
compatibility fallback. The Codex bridge includes its runtime, but the
`codex login` commands below require the Codex CLI on `PATH`:

```bash
npm install -g @openai/codex
```

According to the [official transition notice](https://github.com/google-gemini/gemini-cli/discussions/27274),
Gemini CLI stopped serving Google AI Pro, Ultra, and free individual accounts
on 2026-06-18. The `gemini --acp` adapter now requires a supported
Enterprise/Google Cloud or paid API authentication path; installing the CLI
alone does not grant individual-tier access.

## Usage

### Claude Code

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="claude", model="claude-acp/sonnet") as session:
        response = await session.query("Hello!")
        print(response)

asyncio.run(main())
```

### Gemini

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="gemini", model="gemini-acp/gemini-3-flash-preview") as session:
        response = await session.query("Hello!")
        print(response)

asyncio.run(main())
```

### Codex (OpenAI)

Codex ACP supports an eligible ChatGPT subscription or API-key authentication.
For local ChatGPT authentication, run `codex login` once; the Codex bridge then
uses `~/.codex/auth.json` without requiring `OPENAI_API_KEY`.

```python
import asyncio
from soulacp import ManagedSession

async def main():
    # Example model; availability depends on the account and bridge version
    async with ManagedSession(provider="codex", model="codex-acp/gpt-5.5") as session:
        response = await session.query("Hello!")
        print(response)

asyncio.run(main())
```

**Environment variables** (all optional):

| Variable | Values | Default | Purpose |
|----------|--------|---------|---------|
| `CODEX_REASONING_EFFORT` | Bridge/model-dependent; for example `high` | `medium` when unset | Forwarded reasoning configuration, not a locally validated enum |
| `CODEX_SANDBOX_MODE` | `read-only` / `workspace-write` / `danger-full-access` | (codex default) | File-system permission |
| `CODEX_APPROVAL_POLICY` | `untrusted` / `on-request` / `never` | (Codex default) | Approval prompts |
| `CODEX_NETWORK_ACCESS` | `true` / `1` / `yes` | not injected | Opt in to network in `workspace-write`; other values leave Codex config unchanged |

An explicit `model_reasoning_effort` in `ACPConfig.extra_args` takes precedence;
otherwise SoulACP reads `extra_env` and then the permitted parent environment.
An explicitly empty effort value injects no override. Accepted levels belong to
the installed bridge and model. SoulACP does not inject a Claude effort default;
use `extra_env` for provider-supported controls when needed, without including
credentials in examples or diagnostics.

The following disables approval prompts, allows workspace writes, and enables
network access. Use it only in a trusted workspace:

Bash:

```bash
export CODEX_SANDBOX_MODE=workspace-write
export CODEX_APPROVAL_POLICY=never
export CODEX_NETWORK_ACCESS=true
```

PowerShell:

```powershell
$env:CODEX_SANDBOX_MODE = "workspace-write"
$env:CODEX_APPROVAL_POLICY = "never"
$env:CODEX_NETWORK_ACCESS = "true"
```

For API-key authentication, let Codex persist the selected authentication mode
instead of relying on an environment key alone. Assuming `OPENAI_API_KEY` is
already set:

```bash
# Bash
printf '%s' "$OPENAI_API_KEY" | codex login --with-api-key
```

```powershell
# PowerShell
$env:OPENAI_API_KEY | codex login --with-api-key
```

The same model ID format is used, but model availability depends on the account
and current Codex ACP bridge. Consult the
[official model catalog](https://developers.openai.com/api/docs/models) instead
of relying on legacy aliases.

### Multiple Providers

This example makes two sequential provider calls. SoulACP does not itself
dispatch nested agents, implement a business workflow, or supply human approval
for that workflow; those responsibilities remain with the calling application.

```python
import asyncio
from soulacp import ManagedSession

async def main():
    # Claude for code generation
    async with ManagedSession(provider="claude", model="claude-acp/sonnet") as claude:
        code = await claude.query("Write a sorting algorithm in Python")

    # Gemini for code review
    async with ManagedSession(provider="gemini", model="gemini-acp/gemini-3-flash-preview") as gemini:
        review = await gemini.query(f"Review this code:\n{code}")
        print(review)

asyncio.run(main())
```

## Session Management

### ManagedSession + ProviderSessionStore

ManagedSession uses ProviderSessionStore internally to map `(user_id, provider)` to CLI session IDs:

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="claude", model="claude-acp/sonnet") as session:
        # First request — creates a CLI session and stores the mapping
        await session.query("Remember 42.", user_id="alice")

        # Second request — reuses Alice's CLI session
        await session.query("What number?", user_id="alice")

        # A different user receives a different logical session
        await session.query("Hello!", user_id="bob")

asyncio.run(main())
```

### Custom Session Store

```python
import asyncio
from soulacp import ManagedSession, ProviderSessionStore, FileCache

async def main():
    store = ProviderSessionStore(cache=FileCache("~/.soulacp/sessions.json"))
    async with ManagedSession(
        provider="claude",
        model="claude-acp/sonnet",
        session_store=store,
    ) as session:
        await session.query("Remember 42.", user_id="alice")

asyncio.run(main())  # Closing the session flushes pending file-cache writes
```

### Session Lifecycle

| Event | Behavior |
|-------|----------|
| Successful first request | New CLI session created, mapping stored after pool release (TTL 7 days) |
| Subsequent requests | Reuse the logical session when its mapping is valid and restoration succeeds |
| Concurrent requests for one user/session | Serialized within one `ManagedSession` instance; different sessions can still run concurrently |
| Context overflow | Raise to caller; call `reset_session(user_id)` only after deciding whether to submit a new turn |
| Confirmed stale stored session | Clear that provider mapping once, then explicitly create a fresh session |
| Pre-prompt connection/timeout error | Preserve the mapping and retry the same session within the configured attempt budget |
| Prompt transport/RPC failure | Raise without retry or fallback because side effects may already exist |
| Fallback | Switch provider only before prompt execution (e.g. startup failure); reuse that provider's own stored session |

A logical session ID is not a promise that the original CLI process, child
agents, or in-memory tools survive a disconnect. When restoration is unavailable,
the managed session may clear the unusable mapping and start a fresh session;
applications that require continuity must handle that distinction explicitly.

### Cache Backends

```python
from soulacp import MemoryCache, FileCache

# In-memory (default) — fast, lost on restart
memory = MemoryCache(max_size=10000)

# File-based — persists across restarts
file_cache = FileCache("~/.soulacp/sessions.json", debounce_seconds=1.0)
```

File-cache paths expand `~`; writes use an atomic temp-file replacement and
`ManagedSession.close()` flushes pending debounced mutations. User IDs are
stored as fixed-width pseudonymous SHA-256 key components, with read-time
migration only for unambiguous legacy plaintext keys. Numeric IDs, lowercase
10/32-character hexadecimal IDs, and delimiter-ambiguous legacy identities are
not auto-migrated because the historical formats cannot prove ownership;
affected mappings must be re-established. One `ProviderSessionStore` serializes
its compound lookup, migration, update, clear, and flush operations so a legacy
migration cannot overwrite a newer mapping. Coordinating multiple store or
cache instances remains the external backend's responsibility. `FileCache` normalizes values to
their standard JSON representation before publishing them and returns detached
read snapshots, so later caller mutation cannot change persisted state. Cache
keys must be valid UTF-8 strings; TTLs must be non-negative integers within the
finite runtime range. `FileCache` is a single-owner backend: do not
open the same path from multiple `FileCache` instances or processes.
Deployments with multiple owners should provide a coordinated external
`CacheBackend`. Stored session IDs are not encrypted, so protect the cache file
with appropriate OS permissions and do not share it.

### Low-level Session Control

```python
import asyncio
from soulacp import ACPConnectionPool, ACPConfig, resolve_client_class

async def main():
    config = ACPConfig(provider="claude", model="claude-acp/sonnet")
    client_class = resolve_client_class("claude")

    async with ACPConnectionPool(config, client_class) as pool:
        # Explicit session reuse
        async with pool.acquire() as (client, sid):
            await client.query("Remember 42.")

        async with pool.acquire(session_id=sid) as (client, sid2):
            assert sid2 == sid
            response = await client.query("What number?")
            print(response)

        # Force a new transport without bypassing the hard connection limit
        async with pool.acquire(session_id=sid, fresh=True) as (client, sid3):
            assert sid3 == sid

        stats = pool.get_stats()  # Non-blocking, no health-check I/O
        print(stats)

asyncio.run(main())
```

Native `ACPClientBase` adapters retain the agent's advertised capabilities,
implementation info, authentication methods, and current session config in
`agent_capabilities`, `agent_info`, `auth_methods`, and
`session_config_options`; `session_mode_state` tracks advertised modes and the
current selection. Stable `session_info_update`, `usage_update`, and
`available_commands_update` notifications are validated into `session_info`,
`session_usage`, and `available_commands`. These five session-owned states are
cleared together for a new/load/resume transition and restored together if the
transition fails. Every stable ACP v1 session-update shape, including content
blocks, tool calls, and plans, is structurally validated before state mutation
or callback delivery. `set_update_callback()` remains the raw, receive-ordered hook
for every update, including tool calls, plans, and thought chunks that are not
flattened into response text. Capability-gated `list_sessions()` and
`delete_session()` cover ACP session-history management; `set_config_option()`
updates flat or grouped select and boolean options, while `set_mode()` switches
an advertised legacy mode for backward compatibility. ACP is moving dedicated
mode methods into session config options, so category `mode` config options are
preferred when an agent advertises them. Configured models similarly use an
advertised category `model` select option; an already-selected value emits no
RPC, and the non-standard `session/set_model` method is only a compatibility
fallback when no model option is advertised. Agent-pushed `config_option_update`
and `current_mode_update` events keep both states current. For Claude, an
explicit model supplies `ANTHROPIC_MODEL` before the ACP process starts, and
model selection is checked again after resume. This is best-effort configuration,
not a strict model-lock guarantee: `extra_env` is applied last and can override
`ANTHROPIC_MODEL`; absent, ambiguous, or RPC-rejected model options can leave the
provider's current selection in use. Transport, timeout, and protocol errors
remain visible. Callers requiring an exact model must check the provider's
reported selection rather than treating `ACPConfig.model` as confirmation of
what served a request. Reported metadata is not independent model attestation.
`authenticate()`,
`logout()`, and `close_session()` expose the current ACP lifecycle methods and
reject calls not advertised by the agent. Successful logout or closure of the
active session invalidates pool reuse, so releasing that lease retires the
transport. Session load/resume performs no transport I/O and reports that the
stored session cannot be restored unless the agent advertises `loadSession` or
`sessionCapabilities.resume`; managed sessions then clear that unusable mapping
once and start fresh. The standalone
legacy `CursorCLIClient` exposes only the minimal `ACPClient` protocol.

Only a leading `<provider>-acp/` or `<provider>/` namespace is removed from a
configured model. Nested IDs such as `claude-acp/openai/gpt-5` therefore remain
`openai/gpt-5`. When an executable explicitly returns JSON-RPC `-32601` for the
legacy `session/set_model` fallback, adapters using that fallback warn once and
cache the result for the same adapter/executable; later clients skip the
unsupported request. Skipping an unsupported RPC does not verify that the
requested model was selected.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ACP_PROVIDER` | `claude` | Provider name |
| `ACP_MODEL` | `claude-acp/sonnet` | Model identifier |
| `ACP_CWD` | Current directory | Working directory for the agent subprocess |
| `ACP_POOL_SIZE` | 5 | Maximum retained idle connections |
| `ACP_POOL_MAX_CONNECTIONS` | 8 | Hard open + creating limit per `ACPConnectionPool` instance |
| `ACP_POOL_ACQUIRE_TIMEOUT` | 30 | FIFO capacity wait timeout (seconds) |
| `ACP_POOL_IDLE_TIMEOUT` | 600 | Seconds since release before idle retirement |
| `ACP_POOL_KEEPALIVE_INTERVAL` | 300 | Idle local transport-state check interval; `0` disables |
| `ACP_POOL_MAX_AGE` | 7200 | Soft connection age limit at lease boundaries |
| `ACP_POOL_MAX_USES` | 50 | Soft successful-lease limit |
| `ACP_POOL_CROSS_SESSION_REUSE` | false | Permit explicit resume on a transport bound to another session |
| `ACP_TIMEOUT_CONNECT` | 30 | Connection timeout (seconds) |
| `ACP_TIMEOUT_PROMPT` | 21600 | End-to-end prompt deadline (seconds, 6 hours) |
| `ACP_TIMEOUT_STREAM` | 1800 | No-ACP-activity stream timeout (seconds, 30 minutes) |
| `ACP_INHERIT_PARENT_ENV` | true | Inherit the full parent environment in provider and ACP terminal children |
| `ACP_AUTO_APPROVE` | true | Prefer `allow_once`; false prefers `reject_once` |
| `ACP_ENABLE_FALLBACK` | false | Enable pre-prompt provider fallback |
| `ACP_MAX_RETRIES` | 3 | Total pre-prompt attempt budget; `0` still performs one attempt |
| `ACP_RETRY_BASE_DELAY` | 1 | Retry backoff base (seconds) |
| `ACP_MAX_TURNS` | unset | Optional Claude-only maxTurns override |
| `ACP_OTEL_PROPAGATE` | 0 | Inject active W3C `traceparent`/`tracestate` at subprocess startup when set to `1`; baggage is excluded |
| `ACP_LOG_SENSITIVE_DATA` | 0 | Set exactly to `1` to opt into raw provider diagnostics in debug/error logs |

> **Security:** `ACP_AUTO_APPROVE=true` automatically chooses an allow option
> (`allow_once` preferred) when the agent requests permission. This is not an
> OS sandbox. Set it to `false` for untrusted workspaces and review the provider
> CLI's own permission model.

Cancelled turns return a `cancelled` permission outcome for queued or late
requests, and duplicate option IDs are rejected before selection. This does
not change valid allow/reject policy or make ACP permission selection a human
business-authorization gate.

`pool_size` is not a concurrency limit. Total physical capacity is bounded by
`pool_max_connections` for each pool instance. Each `ManagedSession` creates
one pool per `provider:model` key, so N keys in one instance can reserve up to N
times the per-pool limit; multiple `ManagedSession` instances multiply this
again. There is no process-wide limiter. With
`pool_cross_session_reuse=False`, an anonymous request never receives an idle
client already bound to another logical session. This favors isolation and
makes the pool primarily a session-affinity cache. A requested session ID can
have only one live lease at a time. `ACPConnectionPool` validates and snapshots
its `ACPConfig` when constructed, and each physical client receives a separate
snapshot. Later caller or client mutation therefore cannot change the pool's
validated capacity and lifecycle policy; construct a new pool to apply policy
changes.

Boolean configuration variables parsed by `ACPConfig.from_env()` accept
`true/false`, `1/0`, `yes/no`, or
`on/off`; invalid values raise `ValueError` instead of silently disabling a
feature. OpenClaw does not receive `--session` by default, so each ACP bridge
uses its isolated session key. Setting `OPENCLAW_SESSION_KEY` deliberately opts
into OpenClaw's shared/attached session routing.
Configuration errors report fields and constraints without echoing raw input;
invalid numeric environment values suppress the original parse exception chain.
Session-control rejection messages omit supplied IDs and values; URL parser
chains and strict JSON duplicate-key errors do not expose their input text.
Raw structured exception fields, including `capability`, remain sensitive.

### Provider-specific environment variables

| Variable | Adapter | Purpose |
|----------|---------|---------|
| `MOONSHOT_API_KEY` | Kimi | API-key authentication |
| `MISTRAL_API_KEY` | Vibe | API-key authentication |
| `OPENCLAW_URL` | OpenClaw | Gateway URL |
| `OPENCLAW_TOKEN_FILE` | OpenClaw | Token authentication override via a protected file |
| `OPENCLAW_PASSWORD_FILE` | OpenClaw | Password authentication override via a protected file when no token file is configured |
| `OPENCLAW_SESSION_KEY` | OpenClaw | Explicitly attach/share a Gateway session; unset preserves isolated `acp-bridge:<uuid>` routing |
| `OPENCLAW_VERBOSE` | OpenClaw | Enable verbose bridge logging with `true`, `1`, or `yes` |

`OPENCLAW_URL` must be an absolute `ws://` or `wss://` URL without embedded
credentials. Plain `OPENCLAW_TOKEN` and `OPENCLAW_PASSWORD` launch overrides
are rejected: OpenClaw accepts them only as command-line values, where local
process tools can read them. Use configured Gateway authentication or a
protected credential file instead.

### Programmatic

```python
from soulacp import ACPConfig

config = ACPConfig(
    provider="claude",
    model="claude-acp/sonnet",
    pool_size=5,
    pool_max_connections=8,
    pool_acquire_timeout=30,
    pool_idle_timeout=600,
    pool_keepalive_interval=300,
    pool_max_age=7200,
    pool_max_uses=50,
    pool_cross_session_reuse=False,
    timeout_connect=30,
    timeout_prompt=21600,
    timeout_stream=1800,
    inherit_parent_env=True,
    auto_approve_permissions=True,
    enable_fallback=False,
    additional_directories=["/absolute/path/to/shared"],
    mcp_servers=[
        {
            "name": "local-tools",
            "command": "/absolute/path/to/mcp-server",
            "args": [],
            "env": [],
        }
    ],
)
```

`extra_args` is appended to the subprocess argv without shell expansion and is
visible to local process-inspection tools. Never put credentials in it.
`extra_env` is merged into the subprocess environment and overrides duplicate
keys. Treat both as trusted configuration; do not log them indiscriminately.
`ACPConfig` representations omit the working directory, additional roots, MCP
definitions, extra arguments, and environment overrides to reduce accidental
log exposure. The fields remain directly accessible and must still be handled
as sensitive configuration.
Provider and ACP terminal subprocesses inherit the parent environment by default
for CLI authentication and compatibility. Set `inherit_parent_env=False` (or
`ACP_INHERIT_PARENT_ENV=false`) to retain only a bounded runtime/path/locale
allowlist; then supply required credentials explicitly through `extra_env` or the
provider's own credential store. This limits environment propagation but is not
an OS sandbox. `additional_directories` and `mcp_servers`
are structured constructor-only fields; they intentionally have no environment
string parser. Roots and stdio commands must be absolute. HTTP/SSE MCP servers
require absolute HTTP(S) URLs without embedded credentials; use validated
header entries for authentication. MCP metadata must already use strict JSON
types with string object keys; tuples and non-string keys are rejected instead
of being silently coerced. Remote MCP servers and additional roots are
sent only when the agent advertises the corresponding
capability, otherwise session creation fails before an unsupported request is
sent.

### From Environment

```python
import asyncio
from soulacp import ACPConfig, ManagedSession

async def main():
    config = ACPConfig.from_env()
    async with ManagedSession(
        provider=config.provider,
        model=config.model,
        config=config,
    ) as session:
        print(await session.query("Hello!"))

asyncio.run(main())
```

`ManagedSession` requires `provider` and `model`. When `config` is omitted, it
loads the remaining settings with `ACPConfig.from_env()`, but the constructor's
provider and model override `ACP_PROVIDER` and `ACP_MODEL`. When a config is
provided, all other fields are preserved without hidden environment overrides;
the constructor still replaces `config.provider` and `config.model`. The
pattern above uses the environment-selected provider and model explicitly.

Prompt and stream deadline failures close the underlying transport and are not
automatically retried or sent to a fallback provider. This prevents requests
with an unknown outcome or partial output from being replayed.
For streaming APIs, time spent by the caller processing an already-yielded
chunk is not charged again by the generator-side prompt deadline. A native
ACP `session/prompt` RPC that is still in flight retains its original
end-to-end deadline.
When an individual outbound RPC is cancelled or times out, soulacp first sends
the standard best-effort `$/cancel_request` notification. Incoming
`$/cancel_request` notifications cancel matching host requests before their
reply starts and return JSON-RPC code `-32800`; prompt timeout cleanup still
retires the transport so a late response cannot leak into a later lease.

Native ACP disconnect also attempts best-effort descendant cleanup: POSIX uses
the CLI's separate process group, and Windows uses recorded descendant PIDs
with parent-chain checks. Detached processes, snapshot races, and permission
failures are outside a guaranteed whole-tree cleanup contract. A cancel
notification or a cleanup log entry is not proof that every descendant exited;
an empty Windows snapshot, particularly after the CLI has exited, is not proof
that no tools remain.
Use OS job/container isolation when that guarantee is required. This does not
extend TerminalService or legacy Cursor into a whole-tree termination API.

Native client connect/disconnect operations are serialized. Prompt/stream and
session load/resume operations are also serialized per client so a completed
old prompt cannot overwrite a newly selected session. Concurrent callers cannot
publish duplicate subprocesses, disconnect waits for an in-flight connect, and
owned cleanup completes before caller cancellation is propagated. Pool lease
release and retirement use the same ownership rule even when cancellation
arrives while cleanup is waiting for the pool lock, so capacity cannot remain
stranded in a leased state.
Bounded user-cancellable waits also preserve caller cancellation on Python 3.10
when the awaited operation completes in the same event-loop turn.
Legacy Cursor query cleanup likewise owns child-process reaping through repeated
caller cancellation. It also bounds output-pipe drain after direct process exit
and explicitly closes exited subprocess transports. A failed terminate/kill never releases physical pool
capacity: the client or terminal record remains owned, `close()` reports the
failure, and a later `close()` retries it. `ManagedSession` retains only the
pools and store flushes that failed, so successful cleanup is not repeated.
Native asyncio transports and OpenCode apply the same rule to a subprocess that
arrives after startup timeout: the late handle is claimed, reaped, or retained
for a later cleanup retry. RPC waits also observe direct subprocess exit while
preserving a bounded window for buffered tail frames, so an inherited stdout
pipe cannot hold a request until its full configured deadline. OpenCode
reader-thread callbacks are bound to the dispatch queue that scheduled them, so
a delayed callback from a disconnected transport cannot enter a replacement
transport generation.
Idle health checks inspect local process and pipe state without injecting probe
bytes into the newline-delimited JSON-RPC transport.

ACP v1 initialization must negotiate protocol version `1`, and every advertised
authentication method must have a non-empty agent-provided `id`; soulacp never
synthesizes one. Known advertised capability fields and every stable v1 RPC
response are validated before local session, mode, or configuration state is
committed. Response and known nested `_meta` values must be objects or null;
supported host requests apply the same metadata contract and return `-32602`
for malformed metadata. ACP v1 permission requests must include a valid
`toolCall` and options with non-empty IDs, names, and standard kinds before
soulacp selects one. Explicit pre-v1 ID-only options remain a compatibility
path when every option omits `kind`. ACP stdout and legacy Cursor JSON stdout are decoded as
strict UTF-8, and server request IDs accept the protocol's null, int64, and
string forms while responses remain correlated to soulacp's int64 integer IDs.
Externally decoded JSON also rejects duplicate object keys so fields cannot be
silently replaced during parsing. Inbound and outbound JSON reject invalid
Unicode and non-finite numbers instead of emitting implementation-specific JSON;
OpenCode's inherited `OPENCODE_CONFIG_CONTENT` must likewise be a strict JSON
object. Prompt completion is accepted only from the correlated
`session/prompt` response with a standard
`stopReason` (`end_turn`, `max_tokens`, `max_turn_requests`, `refusal`, or
`cancelled`). Message-end update extensions do not complete a turn. Incoming
session updates and all supported host requests must identify the current session;
stale cross-session messages fail closed. Transport loss after the prompt write
raises `ACPRequestOutcomeUnknownError`; malformed completion raises
`ACPProtocolError`. Any RPC interrupted after its write may have reached the
agent, so the transport is retired after best-effort `$/cancel_request` rather
than being returned to the pool with potentially divergent session state.

ACP filesystem requests honor the v1 absolute `path`; schema-boundary `line=0`
maps to the first 1-based line, and `limit=0` returns no lines. Requests are
contained to `cwd` plus validated `additional_directories` and bound file, text,
write, and directory sizes. Reads accept regular files only and enforce the byte
limit on the opened stream, including files that grow after path validation.
This is best-effort path containment under stable filesystem topology, not
race-resistant symlink protection or an OS sandbox.
Terminal working directories use the same configured roots; requests use
no-shell argv execution, bounded output (including
`outputByteLimit=null` or `0`), the standard `truncated` result flag, and a limit
of 32 unreleased or late-spawning terminal records and 32 concurrent
terminal-wait host requests per client service. A cancelled terminal create
retains its capacity reservation until the late child is reaped. SoulACP owns
the direct child process, and terminal wait follows that child's exit with only
a bounded output-drain window; use OS job/container
isolation when descendant-process cleanup must be guaranteed.

### Pool statistics and OpenTelemetry

`get_stats()` retains the original keys and adds closed/idle/active/checking/
closing state, creating reservations, unreaped transports, waiters, claimed
sessions, capacity, creation/disconnect failures, cleanup retries, session
hits, resume results, keepalive loop failures, evictions, lifecycle peaks, and a policy snapshot
including the effective startup-attempt budget.
Metrics use only low-cardinality attributes; session IDs, prompts, user IDs,
paths, and PIDs are never metric attributes. Built-in provider names are kept;
unknown/custom provider labels are collapsed to `custom` in metrics and spans,
and unrecognized pool-dimension values are collapsed to `other`.

soulacp uses only the OpenTelemetry API. Applications remain responsible for
configuring the SDK, readers, exporters, and backend. Without the optional API
package, all pool instrumentation is a no-op. Instruments are process-wide;
metrics and RPC spans omit session IDs, prompts, error text, user IDs, paths,
and PIDs. Telemetry backend failures are isolated and cannot replace operation
results or exceptions.

RPC span cleanup never receives the original exception or its traceback. Failed
RPC spans retain a bounded error/cancellation category and error status without
an exception event or status description. Development tests install the SDK to
check real in-memory export; the runtime library still only uses the optional API.

### Privacy boundaries

Default SoulACP logs and public exception strings redact provider messages,
RPC data, session IDs, stderr, user IDs, and cache paths. `RPCError` and
`ACPProviderError` and `ACPProtocolError` retain raw fields for programmatic diagnosis; raw text is
rendered only through `format_diagnostic(include_sensitive=True)`. Setting
`ACP_LOG_SENSITIVE_DATA=1` additionally enables raw provider diagnostics in
library logs and should be limited to short-lived, access-controlled debugging.
Protocol errors keep safe reason text public and provider values in `details`.
File-cache persistence errors retain their OSError category and errno, with
redacted public filenames. Their `cause` field retains the original I/O error
for explicit private diagnosis; treat it as sensitive and do not print it.
Response wrappers retain their original exception in `cause`; fallback failures
also retain the earlier primary exception in `primary_cause`. Their default
`__cause__` display chain now contains a safe summary, not the original object.
Code that needs diagnostic identity must use the explicit raw fields. Full Python
tracebacks can still expose source-file locations and caller-added notes and must
be reviewed privately. Partial output is original response content, not a redacted
diagnostic field; protect it as private application data.
Claude model-selection values are omitted from default logs; sensitive opt-in
renders them with escaped line breaks rather than additional log lines.

Before sharing an issue, trace, or test report, remove credentials, authorization
headers, cookies, prompts/responses, session IDs, personal paths, and identifying
data. Do not attach `.env`, provider authentication files, session caches, raw
configuration/state objects, or the output of `format_diagnostic(include_sensitive=True)`.
Use synthetic reproductions and minimum necessary, reviewed diagnostics. The
default redaction is not a guarantee about third-party CLI logs or application
code that prints raw fields. `ACP_AUTO_APPROVE` controls ACP permission selection;
it does not provide a human's approval for an application's business decision.

`ProviderSessionStore` uses memory by default. An explicitly configured
`FileCache` stores provider session IDs unencrypted and uses deterministic
pseudonymous user-key hashes; protect the file with OS permissions and do not
share it across trust domains. Only unambiguous legacy plaintext cache keys are
migrated when accessed; ambiguous mappings fail closed and must be
re-established.

Legacy Cursor CLI prompts are sent through stdin, not argv. Cursor's documented
resume interface still requires the session ID in `--resume`, and explicit
`OPENCLAW_SESSION_KEY` values are likewise command-line metadata. Prefer the
native Cursor ACP adapter when process-list privacy is required. SoulACP agent
subprocesses run as the current OS account and inherit the parent environment
by default; minimal-environment mode does not change that OS trust boundary.
This library is not a credential sandbox.

### Error handling

These are public imports from `soulacp`; catching an error does not establish
whether external side effects occurred:

| Error | Caller responsibility |
|-------|-----------------------|
| `ACPRequestOutcomeUnknownError` | Execution may have started. Do not automatically replay; reconcile external effects first. |
| `ACPPartialResponseError` | `partial_output` is incomplete original content, not a successful answer or public diagnostic. |
| `ACPProtocolError` | Treat malformed completion as failure; keep raw `details` private. |
| `ACPPromptTimeoutError` / `ACPStreamTimeoutError` | The transport is retired; no automatic retry or provider fallback. |
| `RPCError` | `is_retryable` classifies a control-plane error; it does not authorize replaying a prompt with an unknown outcome. |
| `ACPResumeError` | Handle unavailable session restoration explicitly; a new logical session does not establish original-child continuity. |

## Architecture

```text
ManagedSession (high-level API)
  ├── ProviderSessionStore (user→session mapping)
  │   └── CacheBackend (MemoryCache / FileCache)
  ├── ACPConnectionPool (bounded reuse + FIFO backpressure + lifecycle)
  │   └── ACPClientBase (JSON-RPC over stdio subprocess)
  │       ├── 29 ACP adapter implementations (see Supported Agents)
  │       └── CursorCLIClient (legacy non-ACP integration)
  └── Services
      ├── FSService (file system operations)
      └── TerminalService (no-shell argv execution, live bounded output, kill/release)
```

## Testing

```bash
# Default suite, strict warnings and statement/branch coverage
# Provider CLI and model calls are skipped unless explicitly enabled
python -m pytest tests/ -W error -v --cov=soulacp --cov-branch --cov-report=term

# Integration tests (requires installed and authenticated CLIs)
python -m pytest --run-integration tests/test_integration.py -W error -v          # Claude Code
python -m pytest --run-integration tests/test_integration_gemini.py -W error -v   # Gemini
python -m pytest --run-integration tests/test_integration_codex.py -W error -v    # Codex

# All unit + integration + stdio stress tests
python -m pytest --run-integration tests/ -W error -v

# Static checks used by CI
python -m ruff check src/soulacp/ tests/ examples/ tools/
python -m ruff format --check src/soulacp/ tests/ examples/ tools/
python -m pyright
# Bash; in PowerShell, set `$env:PYTHONPATH = "src"` first.
PYTHONPATH=src python -m pyright --verifytypes soulacp --ignoreexternal
python -m bandit -q -r src/soulacp

# Build into a new, empty output directory; do not mix historical releases
python tools/build_release.py --outdir dist/0.2.1
python -m twine check dist/0.2.1/soulacp-0.2.1-py3-none-any.whl dist/0.2.1/soulacp-0.2.1.tar.gz
python tools/check_sdist.py dist/0.2.1/soulacp-0.2.1.tar.gz
```

Repository hooks use the separate Python >=3.11
[hook environment](CONTRIBUTING.md#repository-hook-environment). `make check`
runs lint, type, and security checks; it does not run tests, hooks, audits, or a
build. The guarded build requires a nonexistent output path and never removes
older artifacts; select another path for a later build.

The CI badge links to GitHub Actions, not a receipt for this checkout or a local
deployment. Record the exact source/artifact identity, platform, actual passes,
failures and skips for each run. Local CI reproduction, remote GitHub CI,
installed-wheel tests, and authenticated provider tests are separate results.

Files named `test_integration*`, `test_spike_*`, and `test_stress_*` require
`--run-integration`; the default suite intentionally skips them.

Source tests select this checkout's `src` through `tests/conftest.py`; they do
not by themselves validate an installed wheel. See the
[release identity checklist](CONTRIBUTING.md#release-identity-and-privacy) for
distribution hashes, a clean-install check, and privacy review. Do not overwrite
an existing output directory or upload `dist/*` indiscriminately.

## Project Documents

| Document | Purpose |
|----------|---------|
| [CHANGELOG.md](CHANGELOG.md) | Release history and behavioral changes |
| [SECURITY.md](SECURITY.md) | Security policy and vulnerability reporting |
| [CONTRIBUTING.md](CONTRIBUTING.md) / [CONTRIBUTING_CN.md](CONTRIBUTING_CN.md) | Contribution workflow |
| [GOVERNANCE.md](GOVERNANCE.md) | Project governance and decision-making |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community standards |

## AIXP Labs [aixp.dev](https://aixp.dev)

AIXP Labs develops and maintains the following core projects:

| Project | Description | Website |
|---------|-------------|---------|
| [HSAW](https://hsaw.dev) | Human Sovereignty and Wellbeing — Axiom 0 white paper (foundation) | hsaw.dev |
| [AIZP](https://aizp.dev) | AI Zenith-Zero Protocol — runtime behavioral alignment | aizp.dev |
| [AIIP](https://aiip.dev) | AI Intent Protocol — governed intent trees, priorities and feedback | aiip.dev |
| [AIKP](https://aikp.dev) | AI Knowledge Protocol — long-term knowledge organization, navigation and governance | aikp.dev |
| [AIJP](https://aijp.dev) | AI Job Protocol — schedulable work trees and job graphs | aijp.dev |
| [AINP](https://ainp.dev) | AI Neogenesis Protocol — governable generation projects: plan + content + evidence | ainp.dev |
| [AILP](https://ailp.dev) | AI List Protocol — agent discovery and capability advertising | ailp.dev |
| [AIVP](https://aivp.dev) | AI Value Protocol — international commerce, crypto asset settlement | aivp.dev |
| [AIRP](https://airp.dev) | AI RMB Protocol — Mainland China commerce, RMB licensed settlement | airp.dev |
| [AIBP](https://aibp.dev) | AI Bot Protocol — social communication and trust | aibp.dev |
| [AIAP](https://aiap.dev) | AI Application Protocol — governance and compliance | aiap.dev |
| [AISP](https://aisp.dev) | AI Skill Protocol — directory packages with a canonical AISOP skill entry and enforceable contract bindings | aisp.dev |
| [AISOP](https://aisop.dev) | AI Standard Operating Protocol — flow program definition | aisop.dev |
| [AIXPStore](https://aixpstore.dev) | Official marketplace for AIAP programs and AISP skills — discover, publish, and distribute AI capabilities | aixpstore.dev |
| [SoulSkill](https://soulskill.dev) | AISP skill reference library & multi-CLI plugin distribution | soulskill.dev |
| [SoulAgent](https://soulagent.dev) | Drop-in AI agent invoked directly by any CLI / SDK / IDE | soulagent.dev |
| [SoulBot](https://soulbot.dev) | AI agent runtime & orchestration framework (scheduling, agent-spawn, inter-agent comms) | soulbot.dev |
| [SoulRun](https://soulrun.dev) | AISOP execution engine as one call — runs AIAP programs and AISP skills for any host | soulrun.dev |
| [SoulACP](https://soulacp.dev) | Adapter library — bridging CLI tools and LLM providers **(this project)** | soulacp.dev |

---

## ⚠️ Disclaimer

This software is **experimental** and provided for **research and educational purposes only**. Not intended for production use. Use at your own risk. The authors assume no liability for any damages arising from the use of this software. See [LICENSE](LICENSE) for full terms (Apache 2.0).

---

## License

[Apache License 2.0](LICENSE) - Copyright 2026 AIXP Labs AIXP.dev | SoulACP.dev

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.2.1. www.soulacp.dev

<a id="alignment-philosophy"></a>

## ⚓ Alignment & Philosophy

### Axiom 0: [HSAW | Human Sovereignty and Wellbeing](https://hsaw.dev)

- **No HITL, HSAW.**
  *Human Sovereignty and Wellbeing is Axiom 0, requiring no hypocritical human-in-the-loop.*
- **No w.a.s.h, Real h.s.a.w.**
- **人非蝼蚁，人为道。**
- **We are not beggars. We the People.**
