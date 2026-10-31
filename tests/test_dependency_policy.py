"""Keep security floors and the Python 3.10 runtime/tool separation explicit."""

from pathlib import Path

import pytest
import tomli
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
    assert "pip-audit -r requirements-hooks.txt" in workflow
    assert "pip-audit -r requirements-min.txt" in workflow
    assert "uv export --no-config --frozen --all-extras --no-hashes --no-emit-project" in workflow
    assert 'pip-audit -r "$RUNNER_TEMP/soulacp-lock-audit.txt"' in workflow
    assert 'python-version: ["3.10", "3.11", "3.12", "3.13"]' in workflow
    assert "--ignore-vuln" not in workflow
    with (ROOT / "uv.lock").open("rb") as stream:
        lock = tomli.load(stream)
    crypto = [package for package in lock["package"] if package["name"] == "cryptography"]
    assert crypto
    assert all(Version(package["version"]) >= Version("50.0.0") for package in crypto)
