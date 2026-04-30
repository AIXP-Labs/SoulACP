# Contributing to soulacp

Thank you for your interest in contributing to soulacp!

> ⚠️ **Contribution Status (Current Stage)**
>
> We welcome **discussion through GitHub Issues** at this stage of development.
>
> **External Pull Requests are not currently accepted.** If you have a proposal — bug report, feature idea, new adapter suggestion, or improvement — please open an issue describing it. If we agree it adds value, maintainers will implement it and credit you.
>
> This policy may be revisited in the future.

> **Stage Status (v0.1.x)**
>
> SoulACP is at early development stage. The processes below describe the *target* development model. Initial decisions are made by AIXP Labs core maintainers; community discussion period scales as the contributor base grows.

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/AIXP-Labs/SoulACP/issues) to report bugs, suggest features, or propose new adapters
- For new CLI adapter requests, use the `new-adapter` label
- For bugs, include reproduction steps and SoulACP version (`python -c "import soulacp; print(soulacp.__version__)"`)
- Provide clear descriptions with examples

### Discussion-Driven Development

1. Propose discussion via issue
2. Maintainers evaluate value, feasibility, and Axiom 0 alignment
3. After consensus, maintainers implement the change
4. Contributors are credited in commit / release notes

### Proposing a New CLI Adapter

When proposing a new adapter, include in the issue:

- **Name** + GitHub / website link
- **ACP entry command** (e.g., `acp` subcommand, `--acp` flag, or special mode)
- **Authentication** requirements (API key, OAuth, etc.)
- **Why** the adapter should be added (use case, demand)

Maintainers will assess and implement if approved.

## Guidelines

### Quality Standards

- All new code must include tests (unit + integration where feasible)
- Code style: `ruff` (configured in `pyproject.toml`)
- Type hints required (`py.typed` package)
- Backwards-compatible API changes (no breaking changes between minor versions)
- Zero external dependencies (stdlib only)

### Bilingual Requirement

README and CONTRIBUTING are maintained in English and Chinese. Issue can be in either language.

## Code of Conduct

By participating, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).

## License of Contributions

By submitting (via issue or any future PR), your contribution is licensed under [Apache License 2.0](LICENSE).

Copyright 2026 AIXP Labs AIXP.dev | SoulACP.dev

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.1.2. www.soulacp.dev
