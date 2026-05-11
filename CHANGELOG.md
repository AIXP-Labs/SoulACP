# Changelog

All notable changes to soulacp are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
