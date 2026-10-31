# Contributing to soulacp

Thank you for your interest in contributing to soulacp!

> ⚠️ **Contribution Status (Current Stage)**
>
> We welcome **discussion through GitHub Issues** at this stage of development.
>
> **External Pull Requests are not currently accepted.** If you have a proposal — bug report, feature idea, new adapter suggestion, or improvement — please open an issue describing it. If we agree it adds value, maintainers will implement it and credit you.
>
> This policy may be revisited in the future.

> **Stage Status (v0.2.x)**
>
> SoulACP is at early development stage. The processes below describe the *target* development model. Initial decisions are made by AIXP Labs core maintainers; community discussion period scales as the contributor base grows.

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/AIXP-Labs/SoulACP/issues) to report bugs, suggest features, or propose new adapters
- For new CLI adapter requests, use the `new-adapter` label
- For bugs, include reproduction steps and SoulACP version (`python -c "import soulacp; print(soulacp.__version__)"`)
- Provide clear descriptions with examples

Use synthetic prompts and data. Do not attach credentials, `.env`, authentication
files, session caches, raw provider responses, authorization headers, or private
paths. Check the imported module location locally, but redact identifying paths
before sharing. Report security-sensitive findings privately under
[SECURITY.md](SECURITY.md), not in a public issue.

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
- Unit tests: `python -m pytest tests/ -W error -q`
- Minimum supported development stack: `python -m pip install -e ".[dev]" -c requirements-min.txt`
- Repository hooks: use the separate Python >=3.11 environment below; validate the full checkout without omitting hooks
- Coverage measures statements and branches; the configured project threshold applies to the combined result
- Code style: `python -m ruff check src/soulacp/ tests/ examples/ tools/` and `python -m ruff format --check src/soulacp/ tests/ examples/ tools/`
- Type hints required (`py.typed` package); validate with `python -m pyright`
  and `PYTHONPATH=src python -m pyright --verifytypes soulacp --ignoreexternal`
  (PowerShell: set `$env:PYTHONPATH = "src"` before the second command)
- Security scan: `python -m bandit -q -r src/soulacp`
- Distribution build and clean-install checks: use the release identity checklist below
- Preserve documented APIs and behavior in 0.2.x patches; any proposed breaking change requires the versioning and migration review in [GOVERNANCE.md](GOVERNANCE.md)
- No required runtime dependencies; the OpenTelemetry API remains optional
- The development profile includes the OpenTelemetry SDK for real in-memory
  exception-event privacy tests; it does not become a runtime dependency.

For the configured statement/branch coverage gate, run
`python -m pytest tests/ -W error --cov=soulacp --cov-branch --cov-report=term`.
`make check` covers lint, types and Bandit only; it is not a full CI run. Tests,
hooks, dependency audits and guarded builds must be reported separately.
Provider integration tests require explicit authorization, installed CLIs and
authentication; never enable them merely to remove default skips.

### Repository Hook Environment

The runtime library and development tests still support Python 3.10. Repository
hooks use Python 3.11 or later because the security-fixed `filelock` required by
their `virtualenv` dependency no longer supports 3.10. Do not install an older
filelock or ignore audit findings to combine these environments.
The `hooks` extra deliberately installs no hook tools on 3.10; it is not a
3.10 hook environment. Use the >=3.11 interpreter for every hook command.

```bash
python3.11 -m venv .venv-hooks
.venv-hooks/bin/python -m pip install -e ".[hooks]" -c requirements-hooks.txt
.venv-hooks/bin/python -m pre_commit install
.venv-hooks/bin/python -m pre_commit run --all-files
```

On Windows use `py -3.11 -m venv .venv-hooks` and
`.venv-hooks\Scripts\python.exe` in place of the Unix interpreter path.
Both `requirements-min.txt` and `requirements-hooks.txt` must pass dependency
audit; separating environments does not waive either audit or the hook checks.
`make hooks` uses that isolated interpreter, not `pre-commit` from the ambient
PATH. For a different location, pass `HOOKS_PYTHON="/absolute/path/to/python"`.
Without make, use `python tools/run_hooks.py --python <hook-interpreter>`.
The wrapper refuses Python <3.11 or a missing environment instead of falling
back to a global tool. Git ignore rules protect the documented local environment
and session-cache locations, but are not a substitute for reviewing staged files.

### Release Identity and Privacy

For the current **0.2.1** source target:

1. Check `pyproject.toml`, `src/soulacp/meta.py`, the root `soulacp` entry in
   `uv.lock`, current documentation footers, and the current changelog entry.
   Do not rewrite historical versions or treat changelog dates as upload receipts.
2. Choose a new, nonexistent output directory. The guarded build refuses any
   existing path, keeps failed outputs, and validates only the new archives.
   `make build` uses this entry without implicit cleanup; override `DIST_DIR`
   for a subsequent build. `make clean` remains an explicitly destructive action:

   ```bash
   python tools/build_release.py --outdir dist/0.2.1
   # Alternatively: make build DIST_DIR=dist/another-new-directory
   ```

3. Check both archives' metadata and package `__version__`; compare package
   payloads and record each file's SHA-256 and size. Old `dist` files and generated
   `build`/`egg-info` metadata do not prove the new distributions are correct.
4. Inspect archive contents before sharing. Exclude credentials, authentication
   stores, session caches, local databases, raw logs, private audit/backup folders,
   and identifying local paths. Do not change privacy checks merely to pass a build.
5. Install the exact verified wheel without fetching dependencies into a fresh,
   independent environment outside the checkout. From outside the checkout,
   use that environment's interpreter with `-I` to compare
   `importlib.metadata.version('soulacp')` and `soulacp.__version__`, and inspect
   `soulacp.__file__` locally. Source tests deliberately select `src`; they are
   not a substitute for this distribution check. Keep personal paths private.
6. Record actual tests, failures, skips, platform coverage, and distribution
   identity. A wheel install is not a real-provider integration test. Review
   downstream requirements and lock files independently; do not silently
   upgrade or restart a consuming application's active workers.
7. Upload only explicitly selected files after the maintainer's release decision;
   do not upload `dist/*` indiscriminately. Documentation examples do not themselves
   authorize publication. Share only reviewed, minimal release evidence, never
   credentials or raw private diagnostics.

### Bilingual Requirement

README and CONTRIBUTING are maintained in English and Chinese. Issues can be in either language.
Keep examples, API names, defaults, privacy limits and verification scope aligned,
not merely headings. The AIXP project tables follow the corresponding SoulRun
README tables, with the self-project label changed to SoulACP. Do not make local
CI results or an existing badge stand in for a GitHub Actions or provider result.

Run the offline documentation regressions with
`python -m pytest tests/test_documentation_consistency.py -W error -q`.
Review external source claims separately; a local link check does not prove a
remote page or model is available. Preserve older changelog entries as history.

## Code of Conduct

By participating, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).

## License of Contributions

By submitting (via issue or any future PR), your contribution is licensed under [Apache License 2.0](LICENSE).

Copyright 2026 AIXP Labs AIXP.dev | SoulACP.dev

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.2.1. www.soulacp.dev
