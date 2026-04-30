# SoulACP Ecosystem Governance

The SoulACP (SoulACP) is governed by a decentralized, federated trust model designed to isolate the structure of intelligence from the rules of its application.

## The Tripartite Chain

SoulACP operates on a strict separation of concerns across three authoritative domains. **All layers are licensed under Apache 2.0** for unified patent protection and ecosystem consistency. **This repository** (`SoulACP-Protocol`) governs the soulacp.dev Authority Layer. The aisop.dev Seed Layer and soulbot.dev Executor Layer are maintained in separate repositories under the same Apache 2.0 license.

### 1. The Seed Layer (`aisop.dev`)

The origin of the format.

- **Responsibility**: Defines the underlying `.aisop.json` language specification, Mermaid graph parsing rules, and the System/User execution model.
- **Philosophy**: Neutral, static, foundational. Unconcerned with ethics or application logic.
- **License**: Apache 2.0 — unified across the AIXP protocol family.

### 2. The Authority Layer (`soulacp.dev`)

The source of governance and the steward of Axiom 0.

- **Responsibility**: Maintains the `SoulACP_Protocol.md` structural specification, the `SoulACP_Standard.*.aisop.json` quality gates, and enforces the Zero-Entropy and L0 isolation rules.
- **Philosophy**: Rigorous, uncompromising. Ensures that all compliant intelligence adheres to "Human Sovereignty and Wellbeing."
- **License**: Apache 2.0 — providing patent protection for the governance layer.

### 3. The Executor Layer (`soulbot.dev`)

The reference runtime environment.

- **Responsibility**: Instantiates the AI Agent, resolves tools, manages memory layers, and enforces the `permissions` declared in the SoulACP contract.
- **Philosophy**: Secure, performant, sandboxed.
- **License**: Apache 2.0 — providing patent protection for the runtime layer.

## Axiom 0 Immutability

**Axiom 0: "Human Sovereignty and Wellbeing" is immutable.**

No major, minor, or patch release of the SoulACP protocol may ever modify, weaken, or deprecate the core alignment to Human Sovereignty and Wellbeing. This constraint is absolute and non-negotiable.

Any protocol change request that is determined to compromise, dilute, or bypass Axiom 0 will be rejected regardless of performance benefits, commercial pressure, or technical convenience.

## Versioning

Changes to the SoulACP protocol follow strict Semantic Versioning (SemVer):

- **Major**: Breaking changes to the AISOP format or SoulACP governance rules
- **Minor**: Backward-compatible additions (new patterns, quality rules, capabilities)
- **Patch**: Bug fixes, documentation corrections, non-normative clarifications

The Axiom 0 immutability constraint supersedes all versioning rules.

## Protocol Steering

The SoulACP protocol is maintained by the SoulACP Protocol Organization across the three domains:

| Domain | Role | Scope |
|--------|------|-------|
| `aisop.dev` | Format Steward | `.aisop.json` specification, field definitions |
| `soulacp.dev` | Governance Steward | Protocol rules, quality standards, security model |
| `soulbot.dev` | Runtime Steward | Reference implementation, tool resolution, execution |

### Decision Process

1. **Proposals**: Submit specification change requests via GitHub Issues with the `spec-change` label
2. **Discussion**: Open discussion period (minimum 14 days for normative changes)
3. **Review**: Maintainers review for Axiom 0 compliance, technical soundness, and backward compatibility
4. **Consensus**: Changes require consensus among relevant domain stewards
5. **Documentation**: All normative changes must include updated specification text and an Architecture Decision Record (ADR)

## Communication

- **GitHub Issues**: Primary channel for specification discussions and proposals
- **GitHub Discussions**: Community questions and broader conversations
- **Architecture Decision Records**: Documented in the [`adrs/`](adrs/) directory

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.1.2. www.soulacp.dev
