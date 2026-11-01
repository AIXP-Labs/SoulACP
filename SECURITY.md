# Security Policy

## Supported Versions

Security fixes are applied to the latest released minor version and the
current `main` branch. Users should reproduce reports against the newest
available release before submitting them.

The current checkout targets 0.2.3; a source version or changelog date is not a
publication receipt. Identify the actual installed distribution and source or
artifact revision in a report. Reproductions should use synthetic inputs rather
than real credentials or private session data.

## Reporting a Vulnerability

Report vulnerabilities through GitHub's private security advisory channel:

**[Report a vulnerability on GitHub](https://github.com/AIXP-Labs/SoulACP/security/advisories/new)**

Do not open a public issue for an unpatched vulnerability. Include the affected
SoulACP version, operating system, provider adapter, minimal reproduction,
expected impact, and whether credentials or external side effects are involved.

## Response Targets

- **Acknowledgment**: within 48 hours
- **Initial assessment**: within 7 days
- **Resolution or mitigation plan**: within 14 days

These are response targets, not disclosure deadlines. Release timing depends
on severity, affected providers, and coordination requirements.

## In Scope

- Cross-session data exposure, session confusion, or unsafe prompt replay
- JSON-RPC/ACP validation errors that cross a trust boundary
- Connection-pool races that leak or orphan owned subprocesses
- Workspace containment bypasses in host file or terminal services
- Command, argument, environment, or path injection introduced by SoulACP
- Unsafe cache persistence, permission-selection, or secret-handling behavior
- Vulnerabilities in the published SoulACP Python distributions or CI release path

## Out of Scope

- Vulnerabilities solely in third-party agent CLIs, ACP bridges, models, or
  provider services; report those to their maintainers
- Model-generated content or tool decisions when SoulACP enforced the requested
  protocol and permission policy correctly
- Availability issues caused only by intentionally extreme local configuration
- Reports requiring disclosure of real credentials, private prompts, or data

If a third-party defect combines with SoulACP behavior to create a new boundary
bypass, the SoulACP portion remains in scope.

## Security Boundaries

SoulACP is a process-management and protocol adapter library, not an operating
system sandbox. `FSService` resolves its own file operations under configured
roots, but this is best-effort containment under stable filesystem topology,
not race-resistant protection against concurrent symlink or reparse-point
replacement. An agent or terminal subprocess runs with the permissions of the
current OS account. Native ACP disconnect also attempts descendant cleanup via
a separate POSIX process group or a Windows descendant snapshot with parent-chain
checks. Escaped groups, process/PID races, and access failures prevent a general
whole-tree guarantee; sending a signal or logging an attempt does not prove
that every process exited. TerminalService and legacy Cursor retain their own
direct-child cleanup contracts. Use an OS job, container, or equivalent isolation
when descendant-process cleanup must be guaranteed.
An empty Windows descendant snapshot, including a snapshot taken after the
CLI exited, must not be interpreted as proof that no tools remain alive.
`ACP_AUTO_APPROVE=true` automatically selects an allow option when
available; use `false` for untrusted workspaces and configure the provider
CLI's own sandbox and permission policy.

ACP permission-option selection is not an end user's business authorization.
Permission requests outside an active prompt turn, including requests queued
under a completed or cancelled turn, return cancelled outcomes. Duplicate option
identifiers are rejected before selection. Replies already started cannot be
withdrawn; ACP v1 cannot always identify a previously unseen old permission
request first arriving during a new turn. These checks cover mediated requests,
not every native CLI operation. File reads fail rather than returning a truncated
requested range; existing character, byte, line and containment limits remain.
Applications must implement their own human decision gates; SoulACP does not
infer consent from a prompt, a model response, or a successful permission RPC.

`auto_approve_permissions=False` controls selection for
`session/request_permission`; it does not globally disable `fs/*` or
`terminal/*` host services. Their configured-root, schema and resource checks
are separate from permission-option selection. The built-in services do not
provide a per-path human approval list for sensitive configuration files.
Applications needing that policy must enforce it through their host/provider
integration and OS isolation; do not treat this flag as filesystem read-only mode.

Native authentication is another side-effect boundary: permission-option rejection
does not prevent CLI login from changing settings or cached credentials. The Gemini
adapter requests the first advertised method; Gemini CLI 0.51.0 can call
`clearCachedCredentialFile` before a changed-method refresh succeeds. Review the
[documented warning](README.md#gemini) before connecting; do not diagnose a failed
authentication code as proof of expired credentials or harmless initialization.

Since 0.2.3, text requests use strict UTF-8 and preserve LF/CRLF/CR and BOM within
the unchanged budgets. A rejected invalid encoding or expanded raw-newline range
is not permission to retry through an unmediated tool. Ordinary-file descriptors
are checked before truncation, but filesystem races are not fully eliminated.
Validation rejection leaves an existing target unchanged; I/O failure after a
write starts can leave partial content. Writes are not atomic or crash-durable.
Owned file operations finish before cancellation propagates; permanently blocked
kernel I/O has no guaranteed Python-thread cancellation deadline.

Session cache files contain unencrypted provider session identifiers. Protect
them with OS permissions and do not share them between trust domains.
`FileCache` is a single-owner backend; multiple instances or processes must not
open the same cache path concurrently. User IDs are represented by
deterministic pseudonymous hashes, not encryption or a password-hardening
function; low-entropy IDs may still be guessable.
Legacy session-key migration is limited to identities whose historical key has
one unambiguous owner. Numeric, hash-shaped, or delimiter-ambiguous legacy
mappings fail closed and must be re-established.

`extra_args` is visible in the child process command line and must never contain
credentials. `extra_env` is trusted launch configuration. Provider and ACP
terminal subprocesses inherit the parent environment by default for CLI
authentication and compatibility. `inherit_parent_env=False` (or
`ACP_INHERIT_PARENT_ENV=false`) retains only a bounded runtime/path/locale
allowlist; required credentials must then be supplied through `extra_env` or a
provider credential store. This is environment minimization, not an OS security
boundary; use OS isolation when the child is outside the host's trust domain.
`ACPConfig` representations omit working-directory paths, additional roots,
MCP definitions, extra arguments, and environment overrides to reduce
accidental logging. This is redaction only; the fields remain accessible to
application code.
OpenClaw Gateway overrides require an absolute `ws://` or `wss://` URL without
embedded credentials. Plain token/password launch overrides are rejected; use
configured Gateway auth or protected credential files.

Default logs and public exception strings redact provider-controlled messages,
RPC data, session IDs, stderr, user IDs, and cache paths. Raw fields remain on
structured exception objects and can be rendered explicitly with
`format_diagnostic(include_sensitive=True)`. `ACP_LOG_SENSITIVE_DATA=1` opts
library logs into sensitive provider diagnostics; never enable it in a broadly
accessible or long-retention logging environment. Metric attributes use bounded
keys and values, with unknown pool-dimension values collapsed to `other`.
Metrics and spans never use this opt-in and remain content-free.
RPC span cleanup passes no original exception to the telemetry context manager;
only a bounded failure/cancellation category and error status are retained.
File-cache persistence errors preserve the OSError category and errno but redact
public filenames. Their explicit `cause` retains the original filesystem error
and is sensitive, like other raw diagnostic objects.
If a file-service worker fails while its caller is cancelled, the cancelled
exception retains the original worker error in its explicit `cause` field;
the default display chain uses a redacted summary, even with sensitive logging
enabled. This does not change uncancelled filesystem error behavior.
Response wrappers keep original diagnostic identity in `cause`, and fallback
failures retain the earlier error in `primary_cause`. The default `__cause__`
display chain contains a redacted summary instead of those original objects.
This does not redact Python source-file locations or arbitrary caller-added
notes; keep complete tracebacks private and review them before sharing.
Partial output remains original response content; exception redaction does not
turn `partial_output` into a safe-to-share diagnostic. A raw cause is retained
for explicit private diagnosis, not implicitly forwarded to telemetry or logs.
Protocol validation errors retain provider identifiers/values separately in
`ACPProtocolError.details`; public error strings keep only safe reason text.
Claude model-selection logs omit raw values by default and escape them when
the existing sensitive-diagnostics opt-in is explicitly enabled.
Configuration validation reports fields and constraints without echoing raw
values; invalid numeric environment values suppress the original parse chain.
Session-control rejection messages omit supplied option IDs and values. URL
parse failures suppress the original parser chain, and strict JSON duplicate-key
errors omit the key. Structured exception fields such as `capability` remain
sensitive even when the public message uses a safe label.

Redaction covers the library's documented diagnostic paths, not arbitrary
application output or third-party CLI logging. Raw exception fields,
`extra_env`, MCP headers, session/config objects, authentication files, and
cache contents remain sensitive. Do not attach them to issues, commits,
distributions, or public CI artifacts. Use synthetic inputs and reviewed,
minimal reports; remove prompts, answers, identifiers, personal paths, tokens,
cookies, and authorization headers before sharing. Never place secrets in
command-line examples. An exact model or provider metadata field is not an
independent attestation of the model that served a request.

`ACP_OTEL_PROPAGATE=1` forwards only an active W3C `traceparent` and
`tracestate` at subprocess startup. Baggage and custom propagation fields are
not copied into the child environment.

Legacy Cursor prompts are sent over stdin, but Cursor resume session IDs and an
explicit OpenClaw shared-session key remain command-line metadata because those
CLI interfaces expose no private transport for the values. Prefer native ACP
and isolated OpenClaw routing when process-list privacy matters.

## Coordinated Disclosure

We coordinate fixes and release notes with affected provider maintainers when
needed. Please do not publicly disclose a vulnerability until a fix or agreed
mitigation is available.

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.2.3. www.soulacp.dev
