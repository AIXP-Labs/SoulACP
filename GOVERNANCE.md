# SoulACP Governance

SoulACP is an AIXP Labs maintained Python client library for the Agent Client
Protocol. Governance applies to this repository's library API, provider
adapters, runtime behavior, packaging, tests, and documentation. SoulACP does
not define the ACP specification and does not govern third-party agent CLIs.

## Maintenance Model

AIXP Labs core maintainers make release and merge decisions. The project is in
an early stage and currently accepts proposals and bug reports through GitHub
Issues; the contribution policy in [CONTRIBUTING.md](CONTRIBUTING.md) states
whether external pull requests are accepted at a given time.

Maintainers are responsible for:

- Session isolation, prompt replay safety, subprocess ownership, and cleanup
- ACP/JSON-RPC compatibility and explicit handling of provider deviations
- Public API stability, typing, packaging, and supported Python versions
- Bilingual README and contribution-document alignment
- Security response under [SECURITY.md](SECURITY.md)
- Accurate attribution and Apache 2.0 license compliance

## Decision Principles

Changes are evaluated in this order:

1. Human sovereignty, wellbeing, privacy, and explicit caller control
2. Session isolation and fail-closed behavior at trust boundaries
3. Protocol correctness and provider interoperability
4. Backward compatibility and migration cost
5. Operational reliability, observability, and performance
6. Maintenance cost and consistency with existing code

Prompt execution has external side effects and ACP v1 provides no prompt
idempotency key. A performance optimization does not justify automatic replay
when request outcome is unknown.

## Change Process

1. Open an issue describing the problem, evidence, affected adapters, and
   proposed compatibility impact.
2. Maintainers classify the change as security, bug fix, feature, adapter
   addition, documentation, or breaking API work.
3. Implementation includes focused unit tests and integration coverage where a
   real authenticated CLI is reasonably available.
4. Required checks follow the repository workflow: strict-warning unit tests
   with statement/branch coverage, repository hooks in their supported tool
   environment, Ruff lint/format including `tools/`, Pyright and public type
   completeness, Bandit, guarded distribution build, archive-input checks and
   dependency audits. `make check` is only the lint/type/Bandit subset.
   Provider integration tests are opt-in because they require external
   binaries, credentials and explicit authorization.
5. User-visible behavior is documented in both README languages and the
   changelog before release.

Documentation must describe implemented behavior and its limits, not turn
best-effort model selection, session restoration, or process cleanup into an
unimplemented guarantee. Diagnostic examples and release evidence must use
synthetic or reviewed, redacted data; privacy is not waived by debugging or CI.

Security-sensitive changes may be developed privately under the coordinated
disclosure process.

## Qualification Records

Keep these claims separate: configured CI matrix, actual local execution,
GitHub-hosted runs, installed-distribution checks, downstream deployment and
authenticated provider/model tests. A badge, registry entry or successful build
does not substitute for another scope. Record failures, existing skips, tested
Python/platform combinations and exact source/artifact hashes; do not weaken
assertions, warnings, coverage or dependency audits to obtain a green result.
Do not upload local runtime snapshots, raw diagnostics or private backups.

## Adapter Requirements

New adapters must document an authoritative installation source and exact ACP
entry command, validate initialization and session creation, preserve the
common timeout and cleanup contracts, and avoid provider-specific behavior in
the shared pool unless it is required by the protocol. Registry presence alone
does not guarantee inclusion; the adapter must be testable and maintainable.

Legacy non-ACP integrations must be labeled explicitly and satisfy the same
subprocess ownership and unknown-outcome safety rules.

## Versioning

SoulACP follows Semantic Versioning:

- **Major**: incompatible public API or behavioral contract changes
- **Minor**: backward-compatible features, adapters, and observability fields
- **Patch**: compatible bug, security, packaging, and documentation fixes

Pre-1.0 releases may refine APIs more quickly, but breaking changes still
require explicit changelog and migration guidance.

The current source target is **0.2.3**. A patch must preserve the documented
public API and safety policy. Source metadata, installed metadata, import
location, distribution contents, and downstream lock files are separate
identities to verify. Historical changelog entries retain their own versions;
version footers and existing dates are not publication receipts. Follow the
[release identity checklist](CONTRIBUTING.md#release-identity-and-privacy)
before selecting artifacts. Provider integrations and external availability
must be reported with their actual tested scope, not inferred from a version
string or registry entry.

The 0.2.1 safe-exception-chain adjustment has an explicit migration boundary:
original wrapped errors are available through `cause`, and an earlier fallback
error through `primary_cause`; `__cause__` now displays a safe summary rather
than preserving the original object's identity. This documented change does not
authorize additional compatibility breaks or unsafe prompt replay. Older
changelog entries describe their historical versions, not today's defaults.

### 0.2.3 Text and Error Migration

The accepted 0.2.3 correction uses strict UTF-8 and original LF/CRLF/CR rather
than replacement decoding or platform newline conversion. CRLF consumes two
characters, so some previously normalized ranges now exceed the unchanged budget
and fail safely. Writes no longer translate line endings. Resource concurrency
uses implementation code `10004`, not authentication code `-32000`; applications
must not classify every old server error as retryable or as missing content.
These documented corrections do not authorize budget expansion or a new
authentication-selection policy. Authentication changes need a separate caller
control and compatibility review; documentation warnings are not an implementation
or evidence that a CLI's credentials were never changed.

## Axiom 0

Human Sovereignty and Wellbeing is the project's immutable guiding constraint.
It does not replace concrete engineering review; changes must also satisfy the
testable safety and compatibility contracts described above.

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.2.3. www.soulacp.dev
