# Security Policy

## Supported Versions

Security fixes are applied to the latest released minor version and the
current `main` branch. Users should reproduce reports against the newest
available release before submitting them.

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
current OS account. SoulACP terminates the direct child it owns; use an OS job,
container, or equivalent isolation when descendant-process cleanup must be
guaranteed. `ACP_AUTO_APPROVE=true` automatically selects an allow option when
available; use `false` for untrusted workspaces and configure the provider
CLI's own sandbox and permission policy.

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

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.2.0. www.soulacp.dev
