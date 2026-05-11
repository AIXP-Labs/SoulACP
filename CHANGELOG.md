# Changelog

All notable changes to soulacp are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.5] - 2026-05-11

### Changed
- **Codex client rewritten to mirror Claude pattern**. Now supports
  ChatGPT-account authentication (via ``codex login``) — no longer requires
  ``OPENAI_API_KEY`` env var. Per-session model selection via
  ``session/set_model`` RPC (works with ``gpt-5.5`` and other ChatGPT-auth
  models, no need to edit ``~/.codex/config.toml``). ``session/new`` now
  includes the required ``mcpServers`` field for codex-acp 0.14+.
- **Codex default reasoning effort: ``medium``** (per OpenAI recommendation).
  3 ways to override (priority order):
  (1) ``ACPConfig.extra_args=["-c", 'model_reasoning_effort="high"']`` (Python),
  (2) ``CODEX_REASONING_EFFORT=high`` env var (e.g. ``.env`` driven),
  (3) defaults to ``medium`` if neither is set.
  Five levels supported: ``minimal`` / ``low`` / ``medium`` / ``high`` / ``xhigh``
  (see https://developers.openai.com/codex/config-reference).
- **Codex permission env vars** ``CODEX_SANDBOX_MODE`` +
  ``CODEX_APPROVAL_POLICY`` + ``CODEX_NETWORK_ACCESS`` — match Claude's
  ``--permission-mode dontAsk`` for Codex via ``.env``. Combined with
  ``CODEX_SANDBOX_MODE=workspace-write`` + ``CODEX_APPROVAL_POLICY=never``
  + ``CODEX_NETWORK_ACCESS=true``, Codex runs without per-call approval
  prompts inside the workspace AND can reach the network (for
  google_search, web_browser, pip install, etc.). No defaults injected
  (Codex's own safer ``read-only`` + ``untrusted`` + no-network remain
  in effect unless user opts in). See
  https://developers.openai.com/codex/agent-approvals-security.
- **``session/load`` (Codex) carries spec-compliant params** — now sends
  ``cwd`` + ``mcpServers`` alongside ``sessionId`` (was sending only
  ``sessionId``, which some strict ACP 1.0 implementations rejected).
- **``session/update`` handler documented**. Text chunks are exposed;
  tool_call / plan / thought updates are intentionally not surfaced (out
  of scope — soulacp is a text-reply adapter, not an observability layer).

### Added
- **``ACPConfig.extra_args``** — list of args appended verbatim to the
  CLI subprocess command. Adapter-agnostic mechanism for passing
  CLI-specific tunables (e.g. Codex ``-c model_reasoning_effort="high"``,
  sandbox policy flags) without subclassing or env-var hacks. Empty by
  default; honored by every adapter via ``base_client.connect()``.
- **``ACPConfig.extra_env``** — dict of env vars merged into the CLI
  subprocess environment. Use this for CLIs that ignore argv (e.g.
  ``claude-code-acp`` does NOT forward ``--effort`` — must use
  ``CLAUDE_CODE_EFFORT_LEVEL`` env var instead). Examples::

      # Claude Code: set effort level (5 levels: low/medium/high/xhigh/max)
      ACPConfig(provider="claude",
                extra_env={"CLAUDE_CODE_EFFORT_LEVEL": "high"})

      # Claude Code: disable adaptive thinking (Opus 4.6 / Sonnet 4.6 only)
      ACPConfig(provider="claude",
                extra_env={"CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
                           "MAX_THINKING_TOKENS": "32000"})
- **``client.cancel()`` method** sends ``session/cancel`` notification per
  ACP 1.0 spec. Called automatically by ``disconnect()`` for graceful
  turn abort (was previously a hard pipe close).
- **Test parity across 4 main adapters**: Claude / Gemini / Codex /
  OpenCode each now have 8 integration tests covering connect, streaming,
  multi-turn, model selection, ManagedSession, session reuse, pool stats,
  and resume. Codex added to the mid-stream-disconnect spike parametrize.
- **3 stress tests** (``tests/test_stress_stdio.py``): Windows CRLF safety,
  large-response no-deadlock, progressive streaming.

### Fixed
- ``tests/conftest.py`` now prepends ``src/`` to ``sys.path`` so tests
  exercise the local source (previously tests ran against the
  pip-installed version, masking development changes).
- ``tests/test_imports.py`` no longer asserts a hardcoded version string
  (avoids the assertion going stale on every release).
- ``test_integration_cursor_acp::test_multi_turn`` skips gracefully when
  Cursor backend returns ``resource_exhausted`` (rate limit) — was
  previously a hard fail.
- Mid-stream disconnect spike timeout raised 30s → 60s and degrades to
  ``pytest.skip`` when the backend doesn't deliver a chunk in time
  (test load contention, not soulacp bug).

### Migration — 0.1.3 → 0.1.5

Upgrading from 0.1.3 or 0.1.4? Most users will see no breaking changes; a
few behaviors changed defaults. Quick checklist:

- **Codex no longer requires `OPENAI_API_KEY`.** If you set it, it still
  works for API-key models, but ChatGPT-account login is now the
  recommended path. Run `codex login` once and remove `OPENAI_API_KEY` if
  you don't need it. Use `model="codex-acp/gpt-5.5"` (ChatGPT-auth model)
  for `ManagedSession`.

- **Codex `session/new` now sends `mcpServers: []`.** Required by
  codex-acp 0.14+. If you pinned an older codex-acp and overrode
  `_initialize`, drop the override — 0.1.5 handles it.

- **Codex default reasoning effort is now `medium`** (was unset).
  If you depended on whatever Codex's silent default was, set
  `CODEX_REASONING_EFFORT=minimal` to keep the old behavior, or leave
  it for the OpenAI-recommended balance.

- **Default timeouts are 30 days** (was 60 min from 0.1.3, raised in
  0.1.4). Long-running cognitive sessions no longer time out
  prematurely. Override via `ACP_TIMEOUT_PROMPT` / `ACP_TIMEOUT_STREAM`
  / `ACP_POOL_IDLE_TIMEOUT` env vars if you want shorter limits.

- **New `ACPConfig.extra_args` / `extra_env` fields.** Both default to
  empty (`[]` / `{}`); existing code is unaffected. Use them for
  CLI-specific tunables — see Codex / Claude examples in CHANGELOG
  "Added" above.

- **New `client.cancel()` method + auto-call from `disconnect()`.**
  If you override `disconnect()` in a subclass, make sure to call
  `super().disconnect()` so cancel still fires.

- **OpenCode `session/load` now implemented.** `pool.acquire(session_id=...)`
  restores OpenCode sessions across boundaries (previously silently
  created a new session). If you relied on the old behavior, pass
  `session_id=None` explicitly.

- **New `set_update_callback()` hook on every adapter.** Opt-in — does
  nothing unless you register a callback. Use it to observe raw
  `session/update` events (text chunks, tool_call, plan, thought).

- **New `soulacp.registry`** module with `list_agents()`,
  `list_installed_agents()`, `is_installed()`. Use it to discover which
  ACP CLIs are available on the host without trying to connect.

No deprecations, no removed APIs. If you ran on 0.1.3, `pip install -U soulacp`
should be a drop-in upgrade.

## [0.1.4] - 2026-05-10

### Changed
- **Default timeouts increased** to 30 days (`2,592,000s`) for `timeout_prompt`,
  `timeout_stream`, and `pool_idle_timeout`. Previous defaults (60min / 30min)
  caused premature failures on long-running cognitive workflows. Override via
  `ACP_TIMEOUT_PROMPT` / `ACP_TIMEOUT_STREAM` / `ACP_POOL_IDLE_TIMEOUT` env vars.

## [0.1.3] - 2026-05-01

### Added
- **`RPCError`** exception class capturing full JSON-RPC error context:
  `code`, `message`, `data`, `method`, `msg_id`, `elapsed_ms`,
  `stderr_tail`, `session_id`. Replaces opaque `Exception("Internal error")`
  raised by the previous dispatcher. Exposed via `from soulacp import RPCError`.
- **`RPCError.is_retryable`** property — returns `True` for JSON-RPC standard
  transient codes (`-32603` Internal error, `-32000`~`-32099` server errors).
- **Subprocess stderr ring buffer** (50 lines) per client. Last 10 lines are
  attached to every `RPCError`, so server-side tracebacks are no longer lost.
- **In-flight RPC tracking** (`_inflight` map of `msg_id → (method, start_ts)`)
  used to attribute errors to a specific method and measure latency.
- **OTel `acp.rpc` span** emitted around every `_rpc()` call when
  `opentelemetry` is installed. Soft-noops with zero overhead otherwise.
  Span attributes: `acp.method`, `acp.msg_id`, `acp.session_id`,
  `acp.provider`, `acp.code` (on error), `acp.timeout` (on timeout).
- **`session.is_context_overflow(exc)`** helper — centralised detection of
  provider-specific context-overflow markers (`prompt is too long`,
  `context length`, `context window`, `too many tokens`, `context limit`).
- **`tests/test_rpc_error.py`** — 19 tests covering construction, formatting,
  `is_retryable` decision matrix, retry integration, overflow detection.

### Changed
- **`retry.is_retryable()`** now prefers `RPCError.code` over keyword matching;
  falls back to keyword scan for non-`RPCError` exceptions (back-compat).
- **`session.ManagedSession._execute()`** now retries transient `RPCError`s
  (via `is_retryable`) instead of breaking on first failure.
- **`query_stream()`** now re-raises captured RPC errors *after* draining
  queued chunks, so callers can distinguish a clean end from a server-side
  failure. Previously, mid-stream RPC failures resulted in silent truncation.
- **`_parse_json()`** distinguishes malformed JSON-RPC frames (lines starting
  with `{` that fail to parse → `WARNING`) from non-JSON banner/progress
  output (→ `DEBUG`). Previously both were silently dropped at `DEBUG`.
- **Error log lines** now include structured context:
  - `query`: `sid=<session> chunks=<n> err=<exc>`
  - `query_stream`: `sid=<session> err=<exc>`

### Notes
- Backward-compatible: `RPCError` subclasses `Exception`, so existing
  `except Exception` callers are unaffected. Only callers that switch on
  exception *type* may need adjustment.
- No new runtime dependencies. `opentelemetry` is lazy-imported inside
  `_otel_rpc_span()` and remains optional.

## [0.1.2] - prior release

Initial public baseline (no changelog entries retained).
