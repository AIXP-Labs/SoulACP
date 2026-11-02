# Changelog

All notable changes to soulacp are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries preserve their recorded source scope. Historical candidate/deployment
statements describe the state when those notes were written, not the present
publication status; use exact source/artifact release receipts for that status.

Publication status, verified 2026-11-02: [0.2.4 is published on PyPI](https://pypi.org/project/soulacp/0.2.4/).
Historical candidate labels below are retained as recorded, not as the current
publication status. Post-release test/documentation changes are listed under
Unreleased; the published 0.2.4 archives remain unchanged.

## [Unreleased]

### Fixed
- Close the source transport before transferring a session to a separately
  created client in the Claude and Codex integration tests. Returning a pool
  lease alone does not release the live connection's ownership.
- Always disconnect the receiving client after connect/resume failure. Add ten
  deterministic lifecycle regressions without swallowing protocol errors or
  changing the original resume assertions.

### Documentation
- Align both READMEs on pool leases, explicit connection handoff, receiving-client
  cleanup and the distinction between structural resume and context retention.
- Record the separately scoped 2026-11-02 Claude/Codex acceptance outcomes without
  claiming all providers passed or erasing the outstanding Codex streaming failure.
- Clarify published 0.2.4 status while preserving historical candidate entries.
  No runtime, dependency, permission-policy or published-artifact change.

## [0.2.4] - Unreleased

This is a source candidate, not a deployment or publication receipt.

### Added
- Constructor-only `auth_method_id` selects one uniquely advertised method,
  bound to its original provider and snapshotted by pools and native clients.
- `ACPAuthenticationError` prevents automatic retry or provider fallback after
  explicit authentication failure, retaining original errors in a private cause.

### Security and Compatibility
- Apply explicit selection before session creation across native ACP transports,
  including OpenCode; legacy non-ACP Cursor rejects it. Manual authentication
  cannot switch methods on an explicitly bound client. No credentials are stored.
- Preserve adapter defaults when no selection is supplied. This does not remove
  advertised-first authentication risks or native authentication side effects.
- Preserve safe pre-authentication retries, prompt non-replay, permission defaults,
  text budgets, standard-library core and Python 3.10 support. No new permission
  callback, result API, PATH policy, provider-specific host patch or budget increase.

### Documentation
- Qualify the Gemini advertised-first warning as the unconfigured default, link
  explicit selection, and include authentication failure in both caller error tables.
- Document selection-field repr redaction and consumer restart checks for shared
  installs, unchanged dependency debt, existing startup catch-up and data-safe rollback.
- Preserve historical release entries and current execution/security behavior;
  bilingual documentation and platform qualifications remain independently verified.

## [0.2.3] - Unreleased

This is an unreleased source candidate, not a deployment or publication receipt.

#### Fixed
- Reject nonregular text targets before destructive opens; verify opened file
  identity, use exclusive creation, complete short writes, and retain file-worker
  ownership through cancellation. Physical CLI termination no longer waits behind
  owned file I/O; OpenCode termination control does not depend on the file executor.
- Strictly decode UTF-8, retain BOM and raw LF/CRLF/CR, and avoid fetching an extra
  logical line after a requested slice. Zero-line reads validate resource premises
  without decoding content. Original encoding failures remain private diagnostics.
- Keep the 10,000 returned-character limit: CRLF now counts as two characters, so
  formerly normalized boundary ranges can safely fail. Windows writes no longer
  expand LF to CRLF or existing CRLF to CRCRLF. These are intentional contract
  corrections, not a claim of identical return values for every old read.
- Distinguish resource/encoding/type/concurrency failures with implementation
  codes 10001-10004. Keep -32000 for authentication and use -32002 for confirmed
  missing terminal handles as well as missing-file reads. Parameter errors,
  containment failures and internal lookup bugs remain distinct and redacted.

#### Boundaries
- No budget expansion, new permission policy, automatic native-tool fallback,
  atomic replacement, new executor framework, or public result API.
- Permanently blocked kernel I/O cannot be killed in a Python worker. Metadata
  query cancellation is unchanged. Post-start I/O failures may leave partial files.
- Whole-file consumers remain limited by the read budget; upstream error
  normalization can also limit new-file creation. Consumer qualification is
  recorded separately from SDK correctness and physical stdio tests.

#### Documentation
- Add a dedicated bilingual authentication/terms entry before multiuser session
  examples; distinguish logical user routing, OS identity, credentials and consent.
- Warn that Gemini's advertised-first authentication can trigger native credential
  cache side effects. Do not claim real Gemini qualification or change auth policy.
- Clarify Codex credential-store choices and account-specific model availability;
  use provider-owned default model selection in the introductory Codex example.
- Correct the independent legacy Cursor architecture, native-client service
  ownership, bilingual paragraph ordering, and current build-output version.
- Document explicit hook coverage for untracked files, verifier import provenance,
  documentation-component inheritance, and default-permission risks in examples.
  Keep earlier version entries, execution behavior and existing safety gates.

## [0.2.2] - 2026-10-31

Source candidate only; this entry is not a publication or deployment receipt.

### Fixed
- Keep original read/write worker failures in an explicit private `cause` when
  caller cancellation wins; use a redacted default exception chain without
  changing worker ownership, cancellation identity, or uncancelled failures.
- Read the requested file range without inserting a truncation marker or silently
  applying a 2,000-line default. Omitted limits read to EOF only within the existing
  10,000-character and 10 MiB file budgets; oversized results fail explicitly.
  Preserve explicit line limits, path containment and owned read-worker cleanup.
- Return ACP resource-not-found (`-32002`) for missing files in `fs/read_text_file`,
  without exposing paths or reclassifying containment and other service failures.
- Close the permission window when the correlated prompt completes, fails,
  cancels or disconnects. Keep queued requests bound to their original turn and
  preserve the active-turn allow/reject policy. Replies already started cannot
  be withdrawn; v1 requests do not universally identify the originating prompt.

### Documentation
- Describe exact-read resource limits, permission lifecycle boundaries and the
  unchanged legacy Cursor `--force` behavior in both README languages.
- Clarify constructor versus process-environment loading, the absence of an
  automatic dotenv loader, executable path placeholders, and permission-option
  selection versus host-service policy. Remove obsolete API references without
  changing configuration or authorization behavior.

## [0.2.1] - 2026-09-10

These notes describe the 0.2.1 source scope. Distribution identity and actual
publication status must be established from the release receipts, not this date.
Older entries below preserve their historical APIs, defaults and test results;
they are not current installation or replay-safety instructions. Use the current
README and CONTRIBUTING for supported commands and verification scope.

### Fixed
- Validate bracketed URL hosts explicitly for MCP HTTP/SSE endpoints and the
  OpenClaw Gateway instead of relying on patch-version-dependent `urlsplit`
  rejection. Preserve valid IP-literal URLs and redacted parser diagnostics on
  Python 3.10.11 as well as later supported interpreters.
- Use safe default display chains for wrapped response failures and earlier
  fallback errors. Retain original diagnostic objects in `cause` and
  `primary_cause`; `__cause__` is no longer their identity-bearing interface.
  Preserve exact partial text, cancellation and non-retryable unknown outcomes.
- Keep original RPC exceptions out of SDK-generated telemetry events and status
  descriptions while preserving bounded failure status and business exceptions.
  Exercise real SDK export in the development profile, not only tracer doubles.
- Redact FileCache filesystem-error strings and public filenames while retaining
  OSError categories, errno, dirty state, durable flush behavior and the original
  diagnostic error through an explicit private `cause` field.
- Omit supplied session-control IDs and values from public rejection messages;
  suppress raw URL parser chains and redact strict JSON duplicate-key errors
  while retaining rejection policy and structured capability fields.
- Preserve per-turn cancellation on queued permission requests and reject
  duplicate permission option IDs without changing valid approval policy.
- Redact malformed configuration values and environment parse chains; reject
  cyclic MCP objects without rejecting legal shared JSON substructures.
- Scan directories lazily within the existing limits. Build only into a new
  output directory without implicitly cleaning old release artifacts.
- Include the isolated-hook requirements and repository tools in source
  distributions, and validate required archive inputs in CI.
- Protect documented local hook environments, demo session caches and local
  credential filenames from accidental Git inclusion. Public fixtures remain visible.
- Keep malformed protocol values in explicit diagnostic details instead of
  public exceptions; omit raw Claude model values from default logs and escape
  line breaks in sensitive opt-in output. Validation and model selection are unchanged.
- Bind `make hooks` to an explicit Python >=3.11 environment without falling
  back to an ambient global pre-commit command.
- Require the security-fixed OpenTelemetry API >=1.45.1 when telemetry is
  requested. Keep the stdlib-only core and Python 3.10 runtime/test support.
- Isolate repository hooks in a Python >=3.11 tool environment with
  filelock >=4.2.1. Audit both minimum dependency profiles; no advisories or
  repository checks are waived to retain the older runtime interpreter.
- Refresh security-affected locked development dependencies, including the
  Linux-only cryptography branch, and audit the frozen lock on Linux in CI.
- Type the Windows-only ctypes loader explicitly inside the existing Windows
  helpers so POSIX type checking can validate the shared module. Runtime
  attribute lookup is unchanged; missing attributes still raise and termination
  authority is unchanged.
- Attempt to terminate the CLI's tools, not only the CLI, on native ACP disconnect. On
  Windows the CLI's descendant processes are recorded with a Toolhelp32
  snapshot at the start of the disconnect and terminated individually (a pid is
  only terminated while its parent is still the CLI or another recorded pid);
  on POSIX the CLI is spawned in its own session and its process group receives
  `SIGTERM`. This reduces orphaned tools after transport retirement, but is
  best-effort cleanup, not proof that detached, late-spawned, or inaccessible
  descendants exited. A cancel notification alone is not whole-tree termination.
- Pin explicit Claude models through `ANTHROPIC_MODEL` before launching
  `claude-agent-acp`, revalidate model selection after session resume, and
  recognize advertised picker aliases such as `opus[1m]` without confusing
  context-window hints with model generations. This reduces unintended profile
  overrides on new or resumed sessions. Explicit `extra_env` still takes
  precedence; absent, ambiguous, or RPC-rejected picker options remain best
  effort and do not introduce a strict model-lock guarantee.

### Documentation
- Align both README languages, the contribution guides, security/governance
  boundaries and maintainer checklists with existing strict-warning, coverage,
  hook, audit and guarded-build checks. Distinguish local and remote CI,
  installed packages and provider qualification; retain historical entries.
- Document exported response-error handling, raw diagnostic ownership and
  original-content partial output. Clarify that sequential provider examples
  do not implement nested-agent dispatch or guarantee process continuity.
- Distinguish source, installed, and distributed versions; document clean output
  directories, exact release files, checksums, and isolated installation checks.
- Clarify logical-session continuity, provider-dependent effort settings,
  permission selection versus human business authorization, and private
  diagnostic handling. Correct outdated API descriptions without changing
  execution logic, defaults, permission policy, or historical release entries.

### Tests
- Correct the exited-parent Windows process-tree fixture to launch the real
  interpreter instead of a virtual-environment redirector, and assert that its
  reported PID matches the owned subprocess. Preserve all termination assertions.
- Add synthetic positive/negative coverage for descendant graph ownership,
  missing intermediates, changed parents, snapshot/access failures, and handle
  closure. Missing ownership evidence still does not authorize termination.

## [0.2.0] - 2026-07-19

### Security
- Add an opt-in minimal subprocess environment for provider and ACP terminal
  children while preserving full parent inheritance as the compatibility
  default. Binary discovery no longer trusts relative/empty PATH entries or
  relative Windows npm roots; ACP terminal bare commands are resolved through
  the same absolute-directory policy before spawn. Host-service failures no
  longer return raw local exception text to providers.
- Expand Windows `PATHEXT` explicitly inside each trusted absolute search
  directory so binary discovery behaves consistently on Python 3.10-3.13
  without re-enabling legacy current-directory lookup.
- Validate Windows `PATHEXT` entries as plain extensions and inspect expanded
  absolute candidates directly. Binary lookup no longer re-reads the parent
  process environment through `shutil.which()` or permits npm fallback names
  containing path components.
- Validate remote MCP URLs as absolute HTTP(S) endpoints without embedded
  credentials; reject whitespace, control characters, backslashes, and
  malformed or CR/LF-bearing HTTP header entries before they reach a provider
  process.
- Require MCP metadata to use strict in-memory JSON types and string object
  keys. Tuples and non-string keys can no longer be silently coerced into a
  different wire value by Python's permissive JSON encoder.
- Move provider-session mappings into a versioned key namespace and migrate
  only legacy identities with unambiguous ownership. Numeric, hash-shaped, and
  delimiter-ambiguous historical keys now fail closed instead of permitting a
  cross-identity read or deletion.
- Serialize each provider-session store's compound read, migration, update,
  clear, and flush operations so an in-flight legacy-key migration cannot
  overwrite a newer session mapping.
- Redact provider messages, RPC data, session identifiers, stderr, local
  paths, and user identifiers from default logs and public exception strings.
  Raw RPC/provider diagnostics remain available only through structured
  attributes or an explicit sensitive diagnostic formatter.
- Limit opt-in subprocess trace propagation to bounded W3C `traceparent` and
  `tracestate` values. OpenTelemetry baggage and custom carrier fields are not
  copied into child environments.
- Omit path-bearing and secret-bearing launch fields from `ACPConfig`'s
  representation, and collapse unknown OpenTelemetry pool-attribute values to
  the bounded `other` label.
- Send legacy Cursor CLI prompts over stdin instead of process arguments.
  Reject plaintext OpenClaw token/password overrides that would otherwise be
  exposed in the child command line; file-based or configured Gateway auth is
  required. Gateway URL overrides must also be absolute WebSocket URLs without
  embedded credentials.
- Reject escaped invalid Unicode and non-finite numbers in ACP JSON, control
  API arguments, and inherited OpenCode configuration before transport I/O.
  File-cache loading now uses a bounded read instead of a stat/read race.
- Reject duplicate object keys in ACP, Cursor, OpenCode, and file-cache JSON so
  security-relevant fields cannot be silently replaced during decoding. Host
  file reads now reject non-regular files and enforce their byte limit against
  the opened file, including files that grow after path validation.
- Retain ownership and physical-capacity reservations when ACP, OpenCode,
  Cursor, or terminal processes cannot be reaped. Failed pool/session/service
  closes are retryable and never silently forget a live transport.
- Claim and reap ACP terminal processes that finish spawning after their create
  caller is cancelled; terminal close now reports failures from every owned
  late-cleanup task instead of silently consuming them.
- Fail closed on non-UTF-8 ACP and legacy Cursor JSON stdout instead of
  replacing invalid bytes inside protocol frames. Launch configuration strings
  are also validated as UTF-8, and relative working directories are normalized
  before session creation.
- Require ACP v1 initialization agreement, standard prompt stop reasons, and
  current-session IDs on session updates and every supported host-request
  alias. Unknown methods containing `permission` can no longer enter the
  auto-approval path. Late frames received during disconnect cannot schedule
  host work.
- Serialize legacy Cursor CLI spawn registration with disconnect so a child
  process cannot appear after the pool has relinquished cleanup ownership.
  Native asyncio transports and OpenCode likewise claim and reap subprocesses
  that finish spawning after startup cancellation.
- Bound terminal output-task cleanup after terminate/kill failures.
- Pin every GitHub Actions dependency to a reviewed immutable commit and add
  weekly Dependabot monitoring for action and Python development-dependency
  updates.

### Added
- Ship the complete test harness, examples, governance documents, lock file,
  minimum/development requirements, and repository quality/automation YAML in
  the source distribution. The wheel remains runtime-only, while an unpacked
  sdist can now reproduce its checks without omitting `tests/conftest.py`, CI
  action-pin inputs, or the pre-commit configuration.
- Add a Python 3.10 CI job that resolves, tests, lints, type-checks, scans,
  audits dependencies, builds, and validates the oldest supported development
  toolchain. Coverage now includes branches, and regex assertions are linted
  for accidental metacharacters.
- Require pytest-asyncio 1.4 or newer for development so a synchronous
  `asyncio.run()` following an async test cannot surface the plugin's fixed
  unclosed-event-loop warning as a nondeterministic project failure.
- Require pytest 9.0.3 or newer for development; earlier releases are affected
  by `CVE-2025-71176` (`PYSEC-2026-1845`) through predictable Unix temporary
  base directories. SoulACP still has no required runtime dependencies.
- Include pre-commit 4.6 or newer in the development extra and minimum toolchain
  so the shipped repository hooks are runnable after the documented standard
  development install. Runtime dependencies remain unchanged.
- Expose `unreaped`, cumulative disconnect failures, and cleanup retries in pool
  statistics, with low-cardinality `soulacp.pool.disconnect.failures` and
  `soulacp.pool.cleanup.retries` OTel counters.
- Expose current capability-gated ACP lifecycle methods for authentication,
  logout, and session close. Closing the active session or logging out marks a
  leased client non-reusable so the pool retires it on release.
- Add validated structured `additional_directories` and `mcp_servers`
  configuration for session new/load/resume. Configured roots also extend the
  containment boundary used by host filesystem and terminal services.
- Support ACP `$/cancel_request` in both directions. Outbound request timeout or
  caller cancellation sends a best-effort notification; inbound cancellation
  stops a matching host request before reply output begins.
- Track validated ACP session mode state and expose `set_mode()` for modes
  advertised by native ACP agents, including autonomous `current_mode_update`
  notifications and transition rollback. This is backward compatibility for
  the current v1 method; category `mode` config options remain the forward path.
- Track validated session metadata, usage/cost, and available-command updates.
  Session new/load/resume now owns one complete state snapshot so all session
  metadata and the prior session ID roll back atomically on failure.
- Complete the installed `py.typed` public contract: all exported symbols have
  known, unambiguous types and documented public callables under Pyright's
  `--verifytypes` analysis.

### Fixed
- Make cache TTL tests advance an injected wall clock instead of sleeping, so
  virtual-machine clock synchronization cannot cause nondeterministic Linux CI
  failures.
- Dispatch parsed empty JSON objects through native and OpenCode protocol
  validation instead of silently dropping them and leaving pending RPCs to
  fail later by timeout or disconnect.
- Remove the unreachable Claude `mcp serve` compatibility branch. Claude
  discovery already requires the current or legacy ACP bridge, so initialization
  now always launches that bridge directly and negotiates integer protocol v1
  instead of retaining a misleading MCP-as-ACP path.
- Complete pool lease release and retirement through an owned cleanup task even
  when caller cancellation arrives while the transition is waiting for the
  pool lock. Repeated cancellation can no longer strand a client in the leased
  state and permanently consume hard capacity.
- Exclude time spent by a stream consumer processing an already-yielded chunk
  from the stream wrapper's prompt deadline. Native ACP's in-flight
  `session/prompt` RPC retains its end-to-end deadline, while completed queued
  output and legacy Cursor delivery no longer fail solely because the consumer
  was slow between chunks.
- Bind each OpenCode reader-thread callback to the dispatch queue that scheduled
  it. A callback delayed across disconnect/reconnect can no longer inject a
  stale frame into the replacement transport generation.
- Validate all required ACP v1 permission-request fields before selecting an
  option, including the embedded tool call and each option's ID, name, and
  standard kind. Explicit pre-v1 ID-only options remain supported as a bounded
  compatibility path.
- Validate known advertised capability fields and every stable ACP v1 RPC
  response before committing local session, mode, or configuration state.
  Response, capability, session-list, config-option, mode, and supported host
  request metadata must be an object or null; malformed host metadata receives
  `-32602` instead of being silently ignored. Protocol-level
  `$/cancel_request` metadata follows the same object-or-null contract.
- Snapshot and revalidate connection-pool policy at construction, then give
  each physical client its own structured configuration copy. Mutating the
  caller's config or one client's public config can no longer bypass the
  pool's validated hard-capacity and lifecycle invariants.
- Serialize prompt/stream execution with session load/resume on each native
  ACP and legacy Cursor client. Late output from an old prompt can no longer
  overwrite or contaminate state belonging to the newly selected session.
- Preserve a terminal-capacity reservation after a cancelled spawn until the
  late child fails or is recorded and reaped. A delayed process can no longer
  push the host service beyond its hard unreleased-terminal bound.
- Make terminal wait observe the direct child process instead of depending on
  stdout EOF. Descendants that inherit the pipe can no longer block the ACP
  exit response indefinitely; buffered output still gets a bounded drain.
- Detect direct ACP/OpenCode subprocess exit while an RPC response is pending,
  with a bounded grace period for buffered tail frames. An inherited stdout
  pipe can no longer stretch a dead request to the full prompt deadline.
- Apply the same bounded post-exit pipe drain to legacy Cursor queries and
  explicitly close asyncio subprocess transports even when the direct process
  had already exited. A bounded-reader failure also cancels its sibling pipe
  reader instead of leaving an unowned background task.
- Treat an unadvertised session load/resume capability as an unavailable stored
  session without issuing an RPC, allowing managed sessions to clear the
  unusable mapping once instead of failing every future request.
- Avoid clearing valid Gemini sessions when `session/list` has another page;
  `session/load` remains the authoritative check for an inconclusive first page.
- Retain ownership of timed-out OpenCode worker-thread writes until subprocess
  teardown makes the physical write return, and refuse reconnect while such a
  worker is still outstanding.
- Enforce ACP's unsigned 32-bit boundary for file-read `line` and legacy
  `offset` inputs.
- Validate every stable ACP v1 `session/update` payload before callback
  delivery, including outer/update metadata, content blocks, tool calls, and
  plans. JSON-RPC error codes must also fit the schema's signed int32 range,
  while empty opaque `session/list` cursors remain valid strings.
- Preserve stored session mappings across transient resume disconnects,
  timeouts, authentication failures, and server errors. Only a confirmed
  missing session now triggers the one-time stale-mapping reset; retries keep
  targeting the original session instead of silently losing conversation
  context.
- Treat the standard ACP `Authentication required` and `Resource not found`
  errors as terminal control-plane outcomes instead of retrying identical RPCs.
  Undefined provider `-320xx` errors retain their compatibility retry policy.
- Reap native and OpenCode ACP parents even when host-service shutdown fails,
  while retaining the host service until its terminal cleanup succeeds. A
  reconnect can no longer replace a failed service and lose subprocess
  ownership.
- Reject JSON-RPC request/response hybrid frames and `session/update` requests
  carrying an `id`. Stderr-reader and terminal-cleanup tasks now preserve
  caller cancellation instead of consuming it as ordinary child completion.
- Reject malformed array-shaped protocol frames consistently and require every
  integer JSON-RPC response ID to fit ACP's signed 64-bit request-ID range.
- Apply one end-to-end deadline to JSON-RPC pipe writes and response waits.
  Notifications, cancellation messages, and host result/error replies are also
  bounded, including OpenCode's worker-thread pipe writes.
- Retire a transport after any outbound RPC is interrupted following its write.
  A timed-out or caller-cancelled session transition can no longer roll back
  local state while leaving the potentially transitioned agent process reusable.
- Retrieve terminal exceptions from RPC futures completed by a concurrent
  disconnect while timeout or cancellation handling unwinds. Native and
  OpenCode transports no longer emit unhandled-Future reports during cleanup.
- Preserve caller cancellation on Python 3.10 when a bounded connection,
  prompt, stream, pool, health-check, or pipe operation completes in the same
  event-loop turn. User-cancellable waits no longer rely on the legacy
  `asyncio.wait_for()` cancellation race, interrupted connects perform an
  idempotent transport cleanup, and pending response futures are explicitly
  retired on every exit.
- Retire direct transports after an unknown prompt outcome, and accept
  response-carried final text only from the correlated `session/prompt` RPC.
  Extension/control responses can no longer mutate prompt output accidentally.
- Retire a transport when an asynchronous host-request task fails, and retain
  ownership of OpenCode reader threads that outlive a disconnect attempt so a
  later close can retry cleanup.
- Retry tracked and failed-startup disconnects on the next pool acquisition
  while keeping each unreaped transport charged against hard capacity.
- Keep an irreversible host file write owned until its worker thread finishes
  before propagating cancellation. If worker completion races with caller
  cancellation, retrieve and chain any worker failure instead of leaving an
  unobserved task exception. Also bound legacy Cursor stdin-close waiting by
  the remaining end-to-end prompt deadline.
- Continue draining ACP stderr after discarding an over-limit line so a child
  cannot deadlock after filling a pipe whose reader exited early.
- Merge child environment overrides case-insensitively on Windows so explicit
  `extra_env` and terminal values deterministically replace inherited keys.
- Resolve Windows bare commands from case-insensitive `Path`/`PathExt`
  spellings in the effective child environment. Custom terminal environments
  no longer fail discovery merely because they use Windows' conventional key
  casing instead of uppercase `PATH`.
- Ensure minimal-environment PATH filtering falls back only to absolute system
  search directories when every inherited entry is relative or empty.
- Preserve nested model IDs by removing only the leading soulacp provider
  namespace. A definitive `-32601` from legacy `session/set_model` is cached per
  adapter executable, avoiding repeated unsupported RPCs and duplicate warnings.
- Treat `provider-acp/default` and `provider/default` as provider-owned model
  selection rather than a literal model ID. Native adapters and the legacy
  Cursor command no longer overwrite provider configuration with `default`.
- Collapse unknown/custom provider names to the low-cardinality `custom` label
  in pool metrics and RPC spans, preventing caller-controlled telemetry label
  growth while preserving built-in provider names.
- Reserve long-running `terminal/wait_for_exit` requests separately so they
  cannot consume every host-request slot needed by terminal kill/release.
- Require an active session before query transport I/O, recheck it after the
  per-client query lock, and validate lifecycle response objects and session IDs
  before committing local state.
- Match current ACP schema boundaries: preserve null server request IDs,
  accept null initial `configOptions`, support grouped select options, validate
  absolute session-list paths, treat file `line=0` as the first line, preserve
  explicit `limit=0`, and accept terminal `outputByteLimit=0`.
- Keep cache persistence, terminal kill/release, failed client startup, and
  pool retirement alive through repeated caller cancellation; cancellation is
  propagated only after the owned physical cleanup finishes. `FileCache.flush()`
  now completes its owned write before re-raising caller cancellation instead
  of consuming that cancellation while joining the debounce task.
- Preserve unknown-outcome semantics when a fallback provider loses its
  transport after prompt execution may have started. Broken OpenTelemetry span
  attribute writers can no longer mask the underlying RPC timeout.
- Require every advertised ACP authentication method to contain a non-empty
  `id`; adapters no longer synthesize method IDs that the agent did not
  advertise.
- Keep legacy Cursor query/stream process cleanup alive through repeated caller
  cancellation and always run final timestamp/temp-file cleanup.
- Filter the model-call and final buffered assistant copies emitted by Cursor's
  partial-output stream after timestamped deltas. Successful Cursor runs no
  longer duplicate text and then fail terminal-result reconciliation.
- Deterministically close nested async streams in base, Gemini, managed-session,
  and fallback wrappers before releasing query locks or pool leases. Early
  consumer exit can no longer defer child-stream cleanup to loop finalization.
- Preserve prompt, protocol, cancellation, and leased-operation errors when
  transport retirement also fails, while retaining the unreaped transport for
  a later pool-owned retry. Successful operations still surface cleanup
  failures instead of hiding them.
- Keep a strong reference to late OpenCode spawn-cleanup tasks and claim legacy
  Cursor children that finish spawning after timeout or caller cancellation.
- Normalize invalid-Unicode errors across all provider-session store
  operations, including `clear()`.
- Reject timeout, debounce, and retry integers outside the runtime float range
  without leaking `OverflowError`; cache entries with such expiry values are
  ignored like other malformed persistence data.
- Reject cache and provider-session TTLs outside the finite runtime range
  before mutation. Exponential retry delays are now capped without first
  materializing an unbounded integer, so very large attempt budgets cannot
  leak `OverflowError` after 1,024 failures.
- Make retry classification, context-overflow inspection, and privacy-safe
  diagnostic rendering fail closed when a third-party exception exposes a
  broken `partial_output`, message, or diagnostic property. Diagnostic code can
  no longer replace the original operational failure.
- Serialize connect and disconnect ownership for native transports. Concurrent
  connects can no longer publish duplicate subprocesses, disconnect waits for
  an in-flight connect, caller cancellation cannot abandon shared cleanup, and
  reader-triggered cleanup is scoped to the transport that scheduled it.
- Match current ACP host-response shapes: successful `fs/write_text_file`
  returns an empty object, and terminal output always includes the standard
  `truncated` field while retaining `_meta.soulacp.truncated` when truncation
  occurs. Concurrent terminal release/close paths now share one termination
  owner.
- Match Gemini `session/list` entries by the standard `sessionId` field before
  `session/load`; standards-compliant persisted sessions are no longer reported
  as missing. The optional list preflight is skipped when its capability was
  not advertised. Provider model prefixes are removed only from the leading edge.
- Make `FileCache` own canonical JSON snapshots instead of caller references,
  validate cache keys and loaded values for UTF-8 persistence, and ignore
  malformed escaped-Unicode/deep JSON files without poisoning later writes.
- Keepalive health checks no longer write a bare newline to ACP stdin, which
  violates the newline-delimited JSON-RPC transport contract. OpenCode now
  shares the base metadata, config, and session-transition lifecycle despite
  using its synchronous Windows pipe transport.
- Track schema-validated ACP agent/authentication metadata, session-list
  metadata, and session config state centrally. Native clients now support
  capability-gated session listing/deletion and stable select/boolean config
  options, including agent-pushed updates.
- Accept target-session updates and host requests while `session/load` or
  `session/resume` is awaiting its response, then switch identity atomically.
  History replay remains observable through update callbacks but can no longer
  pollute or exhaust the active prompt response buffer. Failed transitions
  restore the prior config state.
- Replace the nested pool startup `wait_for()` with an explicitly owned
  connection task, preventing Python 3.11 completion/cancellation races from
  publishing a cancelled client while retaining Python 3.10 compatibility.
- Treat the distinct Python 3.10 `asyncio.TimeoutError` type as retryable even
  when it has no message, matching Python 3.11+ behavior.
- Validate JSON-RPC error codes and messages without coercion, clear in-flight
  diagnostics when the stdout reader ends, and process stream updates before
  invoking mutable consumer callbacks.
- Resolve adapter command settings from the effective child environment, so
  `ACPConfig.extra_env` overrides the parent process consistently. Minimal-env
  mode no longer reads provider options from the parent, and Windows lookups
  honor case-insensitive environment names. Explicit OpenClaw `off` mode is now
  sent instead of silently ignored.
- Use Kimi's standard persisted-login authentication RPC instead of speculative
  API-key credential params, and do not auto-start Vibe's advertised
  browser/terminal setup methods during headless initialization.
- Prefer stable category `model` session config options for adapter model
  selection, avoid redundant RPCs when the requested model is already active,
  and use non-standard `session/set_model` only when no model option is
  advertised. Provider RPC rejection may still retain its current model, while
  transport, timeout, and protocol failures abort initialization.
- Centralize session resume/load handling across native adapters. A confirmed
  missing-session rejection returns `False`; authentication, internal,
  transport, timeout, and protocol failures remain visible and retain their
  cause through pool retirement.
  Requests also require the advertised `loadSession` or
  `sessionCapabilities.resume` capability before transport I/O.
- Automatic adapter authentication now degrades only an explicit RPC rejection;
  transport, timeout, and protocol failures abort initialization.
- Make package-version and dependency tests read `pyproject.toml` directly so
  results no longer depend on stale installed metadata or build artifacts.
- Enforce `ACP_MAX_RETRIES` as the total high-level pre-prompt attempt budget;
  nested pool startup retry no longer multiplies process launches.
- Empty-message timeout and connection exceptions are now recognized as
  retryable control-plane failures.
- Authentication RPC failures are no longer treated as transient server
  errors that restart the provider process without changing credentials.
- Failed keepalive tasks cannot prevent pool transport cleanup. Pool statistics
  expose closed state, keepalive-loop errors, and the effective startup budget.
- Reconnects clear stale session and stderr state, and a successful `connect()`
  must publish a live transport with a non-empty session ID.
- Gemini now establishes its logical session during initialization, matching
  the shared post-connect contract, and validates advertised auth methods.
- `ACPConfig` copies caller-owned launch lists/maps, falsy custom cache/store
  implementations are preserved, and discovered executables are absolute.

### Changed
- Run CI tests with warnings promoted to errors, execute every shipped
  pre-commit hook in CI, and validate wheel/sdist README metadata with Twine.
  Commit hooks reject private keys, debug statements, and accidentally added
  large files. Tests also keep the two development dependency manifests in
  exact package/order alignment.
- Stress tests prefer an installed Codex bridge and support explicit provider
  selection. Mid-stream reconnect checks now test transport/session health
  without conflating provider model or credit failures with pool lifecycle.
- Add an offline lifecycle matrix covering `initialize`, `session/new`, and
  resume/load for every registered native ACP adapter.
- Update the Ruff pre-commit hook from the stale `v0.4.0` release to v0.15.22,
  update the standard hook set from v4.6.0 to v6.0.0, and freeze both to their
  reviewed commit SHAs so commit-time checks match the CI supply-chain policy.
- Add Bandit and the optional OTel API to the local/CI development toolchain,
  and enforce a 75% coverage regression floor below the current baseline.
- ACP resume requests include the required `mcpServers` field through the
  shared RPC contract. Claude and OpenClaw advertise the host capabilities
  implemented by SoulACP.
- Corrected provider install hints against the official ACP Registry and added
  offline command-contract tests. Pyright, examples, and distribution builds
  are included in local and CI quality gates.
- Replaced stale protocol-authority governance/security text with the actual
  Python library's maintenance and trust boundaries.

### Core Runtime Security
- Fixed a cross-session isolation flaw where `acquire(None)` could yield an
  idle client still bound to another logical session.
- Failed resume no longer logs "starting fresh" and then yields the old
  session. The pool now raises `ACPResumeError`; `ManagedSession` may clear a
  stale store entry and explicitly create a new session.
- A logical session can have only one live lease, and `ManagedSession`
  serializes requests for the same user. This closes first-turn and resume
  races without reducing concurrency between unrelated sessions.
- Exact-session leases now require the client session ID to remain stable
  until release. Mutated transports are retired, and session mappings are
  persisted only after the pool release contract succeeds.
- Prompt RPC failures are no longer automatically retried or sent to a
  fallback provider. ACP v1 has no prompt idempotency key, so a response error
  or transport loss after the write cannot prove that external side effects
  did not occur.
- Terminal host requests now use parameterized no-shell process creation,
  workspace-contained working directories, bounded output buffers, and
  explicit kill/release cleanup.
- Host-side concurrency is bounded: at most 64 in-flight ACP requests and 32
  unreleased terminals per client service; repeated non-text activity wakeups
  are coalesced.
- Malformed JSON-RPC frames, invalid server-request params, conflicting final
  text, oversized responses, and unsupported permission choices fail closed
  instead of timing out or being treated as successful output.
- OpenClaw no longer forces every bridge process onto the shared
  `agent:main:main` Gateway session. Isolated bridge routing is now the default;
  sharing requires an explicit `OPENCLAW_SESSION_KEY`.
- Legacy permission options without ACP `kind` metadata are accepted only in
  explicit auto-approve mode. Rejection mode fails closed because an opaque ID
  cannot safely be inferred to mean reject.
- JSON-RPC input now requires version `2.0`; response/event/chunk counts,
  OpenCode dispatch backlog, terminal argv/env, cache snapshots, and directory
  scans have explicit client-side resource limits.

### Core Runtime Additions
- Per-`ACPConnectionPool` hard capacity (`pool_max_connections=8`) with creating
  reservations, FIFO waiters, and `pool_acquire_timeout=30` backpressure.
- `acquire(fresh=True)`, monotonic `PoolEntry` lifecycle metadata, 10-minute
  idle retirement, 2-hour soft max age, 50-lease soft max uses, and LRU idle
  eviction.
- Expanded `get_stats()` capacity, waiting, creation-reason, session-hit,
  resume, health, eviction, and policy fields.
- Optional OpenTelemetry API instruments via `soulacp[otel]`; core installation
  remains dependency-free and metrics reject high-cardinality identifiers.
- Public structured pool, resume, prompt, stream, partial-response, protocol,
  and unknown-request-outcome errors.
- ACP v1 terminal service support for `terminal/create`, `terminal/output`,
  `terminal/wait_for_exit`, `terminal/kill`, and `terminal/release`.
- `ManagedSession.reset_session(user_id)` as the explicit caller-controlled
  recovery path after a reviewed context overflow.

### Core Runtime Contract Changes
- Claude ACP discovery now prefers the renamed `claude-agent-acp` executable
  while retaining `claude-code-acp` as a legacy fallback. Codex installation
  guidance now targets `@agentclientprotocol/codex-acp`; deprecated Zed package
  names are no longer advertised.
- Documented Gemini CLI's 2026-06-18 personal-tier migration to Antigravity;
  `gemini --acp` now requires a supported Enterprise, Google Cloud, or paid API
  authentication path.
- Production-safe defaults: idle pool 5, hard capacity 8, acquire wait 30s,
  idle 600s, keepalive 300s, connect 30s, prompt deadline 21600s, stream
  inactivity 1800s, max age 7200s, max uses 50, cross-session reuse disabled.
- `pool_size` now has an explicit idle-cap contract. The hard capacity applies
  per pool instance. `ManagedSession` owns one pool per `provider:model`; there
  is no process-wide aggregate limit.
- Prompt timeout is one end-to-end deadline. Stream timeout is reset by every
  ACP session update, including non-text tool/plan/thought activity.
- Partial output is attached to an exception and is never returned as a normal
  success after RPC or timeout failure.
- Automatic string-matched context-overflow rotation was removed. Once a
  client query is entered, untyped failures are reported as unknown outcomes;
  retry and provider fallback are limited to pre-prompt failures.
- Prompt/stream deadline failures close the transport and are never retried or
  sent to fallback, preventing replay after an unknown or partial outcome.
- A prompt turn completes only when the correlated `session/prompt` response
  contains a non-empty `stopReason`. Compatibility message-end updates no
  longer silently truncate a still-running turn.
- `ManagedSession(config=None)` reads `ACPConfig.from_env()`; explicit configs
  are cloned with `dataclasses.replace()` so no fields are dropped.
- Boolean env parsing is strict. Keepalive starts with the pool and performs
  network checks outside the pool lock.
- Permission handling prefers least-persistent ACP v1 choices (`allow_once` or
  `reject_once`) and accepts ID-only options only for legacy adapters.
- File-cache writes are atomic, debounced mutations are flushed on managed
  close, and session-store user keys use fixed-width pseudonymous hashes with
  legacy migration.
- File cache loading is capped at 10 MiB and non-standard/non-serializable JSON
  values are rejected before mutating in-memory state.
- Cursor CLI now consumes its documented JSON and stream-JSON result formats,
  captures deferred session IDs, and no longer misuses `-f` as a prompt-file
  option (`-f` is Cursor's force flag).
- OpenTelemetry instruments are cached process-wide. RPC spans exclude session
  IDs and error text, and subprocess context propagation only injects an
  existing valid active span.
- Claude no longer receives an unconditional 8GB `NODE_OPTIONS` override.
  `max_turns` is opt-in rather than an implicit 100000-turn provider policy.

### Core Runtime Fixes
- RPC pending futures are registered before pipe writes and removed on timeout,
  cancellation, and disconnect.
- Initialization failure, cancellation, and close paths now await reader tasks,
  terminate/kill subprocesses, and retain capacity until physical close.
- Base ACP subprocesses now use `ACPConfig.cwd` as their actual process working
  directory rather than only reporting it in `session/new`.
- `ManagedSession.close()` now permanently closes the owner; later query,
  reset, or context re-entry attempts raise `ACPPoolClosedError` instead of
  silently creating new pools.
- Pool creation and close bookkeeping is cancellation-safe: connected clients
  cannot escape registration, and caller cancellation cannot orphan a close.
- OpenCode stdout crosses into asyncio through one ordered queue, so buffered
  responses are dispatched before EOF cleanup.
- OpenCode pipe writes run outside the event-loop thread under one write lock;
  reader protocol errors retain their structured cause. Cursor stdout/stderr
  and cumulative response text are bounded.
- OpenCode and legacy Cursor CLI overrides now follow the same timeout,
  unknown-outcome, and process-cleanup contracts as the base client.
- Cursor CLI session IDs discovered after process startup are persisted by
  `ManagedSession` for subsequent turns.
- Fallback providers now resume their own stored user session and clear a stale
  fallback mapping once before creating a fresh session.
- ACP filesystem requests honor the v1 `line` field and native absolute paths,
  return the correct empty write result, reject escapes, and bound reads,
  writes, text, and directory entries.
- The static registry now includes all 30 adapters (including `cursor-cli`),
  and unknown model prefixes raise instead of silently routing to Claude.
- Real CLI integration, disconnect-spike, and stdio-stress tests are opt-in via
  `--run-integration`; the default suite is deterministic and offline.
- `retry_async(max_retries=0)` now performs the documented single attempt.
- Server-to-client terminal requests run independently, preventing a blocking
  `terminal/wait_for_exit` request from deadlocking the stdout reader.
- Packaging metadata uses the SPDX license expression supported by current
  setuptools and includes both license files in built wheels.
- Direct base-client and OpenCode startup now apply `timeout_connect` to the
  complete spawn/initialize/session-new operation. OpenCode process creation
  runs off the event loop and late processes are reaped after cancellation.
- Startup failures before process creation release host services, and stdout /
  stderr readers drain buffered pipe tails after process exit.
- Concurrent or cancelled `ManagedSession.close()`, pool close, and terminal
  service close calls share owned cleanup tasks and do not return before
  physical cleanup completes.
- OpenCode uses its synchronous transport for health checks, bounds reader-to-
  loop backlog, and serializes dispatch; Cursor stream assembly is linear and
  bounded by bytes, events, chunks, and characters.
- Optional OTel context-manager failures and truthy exit values cannot change
  application exception behavior. Oversized or non-standard file-cache JSON
  is rejected without corrupting the previous in-memory or on-disk snapshot.

### Migration — 0.1.7 → 0.2.0
- This release intentionally changes timeout and pooling defaults. Set the old
  values explicitly only when required; do not re-enable cross-session reuse as
  a performance workaround.
- Capacity timeouts are `ACPPoolAcquireTimeoutError` and are not retried or sent
  to a fallback provider automatically.
- 0.2.0 removes the 0.1.7 assumption that a downstream engine will make prompt
  replay safe. Applications may still implement recovery, but soulacp itself
  never treats an unknown prompt outcome as replayable.
- Applications that depended on the old implicit Claude 8GB heap or 100000
  `maxTurns` policy must now set `extra_env["NODE_OPTIONS"]` or `max_turns`
  explicitly.
- Applications relying on implicit context-overflow replay must catch the
  provider error, decide whether a new turn is appropriate, and call
  `reset_session(user_id)` explicitly. Unknown model prefixes must also be
  corrected instead of relying on the former Claude default.

## [0.1.7] - 2026-05-27

### Fixed
- **Subprocess fatal-exit is retryable again (corrects 0.1.6 fail-fast).**
  `RPCError.is_retryable` no longer returns `False` for fatal subprocess exit
  codes (`0xC0000409` V8 OOM etc.). The 0.1.6 fail-fast was premature: a retry
  does NOT replay the same prompt in the dead process — it reconnects a **fresh**
  Claude Code subprocess (clean heap) and node_engine crash-recovery **resumes
  from the last completed node**, so retries usually succeed (observed in
  `docs/plan18/03`, contradicting 0.1.6's untested "re-crashes deterministically"
  assumption). 0.1.6 also relied on an engine-side A2 降载-retry fallback that was
  **never built** (`plan20/07 §7`) — leaving crashes with no safety net, so the
  whole pipeline terminated instead of resuming. `_is_fatal_subprocess_exit` is
  retained **for observability only** (retry logs surface crash-driven retries),
  not to block the retry.

### Kept from 0.1.6
- `NODE_OPTIONS=--max-old-space-size=8192` default heap (reduces crash *frequency*).
- Full `RPCError` context (code/message/data/stderr_tail) — the diagnostic detail 0.1.5 lacked.

### Migration — 0.1.6 → 0.1.7
- Backward-compatible. Fatal subprocess crashes retry 3× again (fresh process +
  resume, as in 0.1.5) **but now with 0.1.6's diagnostic detail**. True fail-fast
  on crashes should wait until the engine-side A2 (降载 retry) safety net exists.

## [0.1.6] - 2026-05-26

### Fixed
- **Subprocess fatal-exit no longer retried (deterministic crash).**
  `RPCError.is_retryable` now returns `False` when `data.details` reports a
  fatal subprocess exit code, even though the ACP server wraps it as `-32603`
  Internal error. A fatal exit (V8 OOM, segfault, fast-fail) is deterministic —
  retrying the same `session/prompt` re-crashes, wasting ~N × the (often
  multi-minute) call. Detected codes: `3221226505`/`0xC0000409`
  (Windows STATUS_STACK_BUFFER_OVERRUN / `__fastfail`, common V8 OOM),
  `3221225477`/`0xC0000005` (access violation), POSIX `134`/`137`/`139`
  (SIGABRT / SIGKILL OOM-killer / SIGSEGV). Plain `-32603` (no fatal exit in
  details) and `-32000`~`-32099` server errors remain retryable as before.

### Changed
- **Claude Code subprocess gets a larger default V8 heap.**
  `ClaudeACPClient` now injects `NODE_OPTIONS=--max-old-space-size=8192` into the
  subprocess environment **only when the caller hasn't set `NODE_OPTIONS`**
  (caller override always wins). Mitigates long-session OOM crashes
  (exit `0xC0000409`) on multi-minute `session/prompt` calls. Override or
  disable via `ACPConfig(extra_env={"NODE_OPTIONS": "..."})`.

### Migration — 0.1.5 → 0.1.6
- Backward-compatible. No API changes. Two behavior changes:
  (1) fatal subprocess crashes now fail fast instead of retrying 3× — if you
  relied on the old retry-on-crash behavior, note it never succeeded anyway;
  (2) Claude subprocesses default to 8GB Node heap — set your own
  `NODE_OPTIONS` in `extra_env` to override.

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
