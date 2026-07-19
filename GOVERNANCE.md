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
4. Required checks are unit tests, Ruff lint/format, Pyright, and distribution
   build. Provider integration tests are opt-in because they require external
   binaries and credentials.
5. User-visible behavior is documented in both README languages and the
   changelog before release.

Security-sensitive changes may be developed privately under the coordinated
disclosure process.

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

## Axiom 0

Human Sovereignty and Wellbeing is the project's immutable guiding constraint.
It does not replace concrete engineering review; changes must also satisfy the
testable safety and compatibility contracts described above.

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.2.0. www.soulacp.dev
