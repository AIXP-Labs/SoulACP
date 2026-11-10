"""Keep security floors and the Python 3.10 runtime/tool separation explicit."""

import argparse
import re
import shlex
from pathlib import Path

import pytest
import tomli
import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def project():
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return tomli.load(stream)["project"]


def requirements(filename):
    return {
        req.name: req
        for line in (ROOT / filename).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for req in [Requirement(line)]
    }


def test_runtime_and_test_profile_keep_python310_without_hook_dependencies():
    metadata = project()
    assert "3.10" in SpecifierSet(metadata["requires-python"])
    assert metadata.get("dependencies", []) == []
    dev = {Requirement(req).name for req in metadata["optional-dependencies"]["dev"]}
    assert {"pre-commit", "filelock", "virtualenv"}.isdisjoint(dev)
    assert {"pytest", "pytest-asyncio", "pytest-cov", "opentelemetry-api"} <= dev


@pytest.mark.parametrize("version,allowed", [("1.20.0", False), ("1.45.0", False), ("1.45.1", True), ("1.45.2", True)])
def test_telemetry_security_floor_in_all_install_profiles(version, allowed):
    metadata = project()
    for extra in ["dev", "otel"]:
        req = next(
            Requirement(r)
            for r in metadata["optional-dependencies"][extra]
            if Requirement(r).name == "opentelemetry-api"
        )
        assert (version in req.specifier) is allowed
    req = requirements("requirements-dev.txt")["opentelemetry-api"]
    assert (version in req.specifier) is allowed
    assert str(requirements("requirements-min.txt")["opentelemetry-api"].specifier) == "==1.45.1"


@pytest.mark.parametrize(
    "version,allowed", [("3.32.7", False), ("4.1.0", False), ("4.2.0", False), ("4.2.1", True), ("4.2.4", True)]
)
def test_hook_dependency_rejects_affected_filelock_versions(version, allowed):
    hooks = {Requirement(req).name: Requirement(req) for req in project()["optional-dependencies"]["hooks"]}
    assert {"pre-commit", "filelock"} == hooks.keys()
    assert (version in hooks["filelock"].specifier) is allowed
    minimum = requirements("requirements-hooks.txt")
    assert str(minimum["filelock"].specifier) == "==4.2.1"
    assert str(minimum["pre-commit"].specifier) == "==4.6.0"


def test_hook_markers_cannot_install_an_affected_fallback_on_python310():
    for value in project()["optional-dependencies"]["hooks"]:
        req = Requirement(value)
        assert req.marker is not None
        assert not req.marker.evaluate({"python_version": "3.10"})
        assert req.marker.evaluate({"python_version": "3.11"})


def test_ci_keeps_both_audits_and_python310_matrix():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'pip install -e ".[dev,hooks]"' in workflow
    assert "python tools/audit_lock.py" in workflow
    assert 'python-version: ["3.10", "3.11", "3.12", "3.13", "3.14", "3.15"]' in workflow
    assert "--ignore-vuln" not in workflow
    with (ROOT / "uv.lock").open("rb") as stream:
        lock = tomli.load(stream)
    crypto = [package for package in lock["package"] if package["name"] == "cryptography"]
    assert crypto
    assert all(Version(package["version"]) >= Version("50.0.0") for package in crypto)


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_ci_does_not_persist_repository_credentials_or_start_provider_integrations():
    data = workflow()
    assert data["permissions"] == {"contents": "read"}
    checked = 0
    for job in data["jobs"].values():
        for step in job["steps"]:
            action = step.get("uses", "")
            if action:
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action)
            if action.startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False
            command = step.get("run", "")
            if "pytest " in command:
                arguments = shlex.split(command)
                assert "--run-integration" not in arguments
                assert arguments[arguments.index("-m") + 1] == "not integration"
                checked += 1
    assert checked >= 3


def test_ci_runs_the_frozen_stack_on_both_platforms_with_a_patched_package_manager():
    data = workflow()
    job = data["jobs"]["locked-dependencies"]
    assert set(job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "uv sync --no-config --frozen --all-extras" in commands
    assert "uv pip check" in commands
    assert "uv run --no-config --frozen --all-extras pytest" in commands
    versions = [
        Version(value)
        for ci_job in data["jobs"].values()
        for step in ci_job["steps"]
        for value in re.findall(r"\buv==([0-9.]+)", step.get("run", ""))
    ]
    assert versions and all(version >= Version("0.13.4") for version in versions)


def test_ci_checks_lock_freshness_before_frozen_reproduction():
    steps = workflow()["jobs"]["locked-dependencies"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert commands.index("uv lock --no-config --check") < commands.index("uv sync --no-config --frozen")


def test_ci_audits_every_locked_marker_branch_without_platform_export_filtering():
    steps = workflow()["jobs"]["quality"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "python tools/audit_lock.py" in commands
    assert "uv export" not in commands


MINIMUM_AUDITS = [
    ("quality", "requirements-hooks.txt", "hooks"),
    ("minimum-dependencies", "requirements-min.txt", "minimum"),
]


def assert_minimum_audit_policy(job, requirement, cache_leaf):
    commands = [
        shlex.split(line)
        for step in job["steps"]
        for line in step.get("run", "").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    audits = [
        arguments
        for arguments in commands
        if arguments[0] == "pip-audit" or arguments[:3] == ["python", "-m", "pip_audit"]
    ]
    assert len(audits) == 1, "minimum profile must have exactly one audit command"
    arguments = audits[0]
    assert arguments[:3] == ["python", "-m", "pip_audit"], "audit must use the selected interpreter"
    parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    parser.add_argument("--strict", action="count", default=0)
    parser.add_argument("--output", action="append", default=[])
    parser.add_argument("--cache-dir", action="append", default=[])
    parser.add_argument("-r", "--requirement", action="append", default=[])
    options, unknown = parser.parse_known_args(arguments[3:])
    assert not unknown, "audit cannot add skip or suppression options"
    assert options.strict == 1, "skipped dependencies must fail the audit"
    assert options.output == ["stdout"], "inherited output settings must not hide the report"
    assert options.cache_dir == [f"$RUNNER_TEMP/soulacp-pip-audit-{cache_leaf}"]
    assert options.requirement == [requirement], "audit must resolve the declared minimum profile"


@pytest.mark.parametrize("job_name,requirement,cache_leaf", MINIMUM_AUDITS)
def test_ci_minimum_audits_are_strict_with_explicit_output_and_task_cache(job_name, requirement, cache_leaf):
    assert_minimum_audit_policy(workflow()["jobs"][job_name], requirement, cache_leaf)


@pytest.mark.parametrize("mutation", ["strict", "output", "cache", "interpreter", "suppression", "missing"])
def test_minimum_audit_policy_rejects_weaker_commands(mutation):
    arguments = [
        "python",
        "-m",
        "pip_audit",
        "--strict",
        "--output",
        "stdout",
        "--cache-dir",
        "$RUNNER_TEMP/soulacp-pip-audit-hooks",
        "-r",
        "requirements-hooks.txt",
    ]
    job = {"steps": [{"run": shlex.join(arguments)}]}
    assert_minimum_audit_policy(job, "requirements-hooks.txt", "hooks")
    if mutation == "strict":
        arguments.remove("--strict")
    elif mutation == "output":
        arguments[arguments.index("--output") + 1] = "private-report.json"
    elif mutation == "cache":
        arguments[arguments.index("--cache-dir") + 1] = "user-cache"
    elif mutation == "interpreter":
        arguments[:3] = ["pip-audit"]
    elif mutation == "suppression":
        arguments.extend(["--ignore-vuln", "SYNTHETIC-ADVISORY"])
    else:
        arguments.clear()
    job["steps"][0]["run"] = shlex.join(arguments)
    with pytest.raises(AssertionError):
        assert_minimum_audit_policy(job, "requirements-hooks.txt", "hooks")
