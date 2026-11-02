"""Keep supported interpreters, both CI platforms and strict test gates aligned."""

import copy
import json
import re
from pathlib import Path

import pytest
import tomli
import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ["3.10", "3.11", "3.12", "3.13", "3.14", "3.15"]
PLATFORMS = ["ubuntu-latest", "windows-latest"]


class _UniqueKeySafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        assert len(keys) == len(set(keys)), "duplicate workflow mapping key"
        return super().construct_mapping(node, deep=deep)


def _assert_support_contract(project, workflow):
    assert project["requires-python"] == ">=3.10", "the minimum interpreter contract changed"
    declared = [
        match.group(1)
        for classifier in project["classifiers"]
        if (match := re.fullmatch(r"Programming Language :: Python :: (3\.\d+)", classifier))
    ]
    assert sorted(declared) == VERSIONS, "interpreter classifiers are missing or duplicated"
    loader = _UniqueKeySafeLoader(workflow)
    try:
        parsed = loader.get_single_data()
    finally:
        loader.dispose()
    job = parsed["jobs"].get("test")
    assert job, "the existing test job is missing"
    strategy = job["strategy"]
    for key, expected in (("python-version", VERSIONS), ("os", PLATFORMS)):
        assert sorted(strategy["matrix"][key]) == expected, "test matrix lost or duplicated a supported target"
    assert any(step.get("with", {}).get("python-version") == "${{ matrix.python-version }}" for step in job["steps"])
    assert strategy["fail-fast"] is False
    assert "continue-on-error" not in job
    commands = [step.get("run", "") for step in job["steps"]]
    assert any("pytest tests/ -W error" in command and "--cov-branch" in command for command in commands)


def _valid_inputs():
    project = {
        "requires-python": ">=3.10",
        "classifiers": [f"Programming Language :: Python :: {version}" for version in VERSIONS],
    }
    workflow = "\n".join(
        [
            "jobs:",
            "  test:",
            "    strategy:",
            "      fail-fast: false",
            "      matrix:",
            "        os: " + json.dumps(PLATFORMS),
            "        python-version: " + json.dumps(VERSIONS),
            "    steps:",
            "      - uses: actions/setup-python@synthetic-immutable-fixture",
            "        with:",
            "          python-version: ${{ matrix.python-version }}",
            "      - run: pytest tests/ -W error --cov=soulacp --cov-branch",
            "",
        ]
    )
    return project, workflow


def test_source_metadata_and_ci_cover_python310_through315_on_both_platforms():
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomli.load(stream)["project"]
    _assert_support_contract(project, (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_support_contract_accepts_the_complete_strict_matrix():
    _assert_support_contract(*_valid_inputs())


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_old_classifier",
        "missing_314_classifier",
        "missing_315_classifier",
        "duplicate_classifier",
        "higher_minimum",
        "upper_bound",
        "missing_old_matrix",
        "missing_314_matrix",
        "missing_315_matrix",
        "duplicate_matrix",
        "missing_windows",
        "missing_ubuntu",
        "continue_on_error",
        "warnings_relaxed",
        "branch_gate_removed",
        "interpreter_not_bound",
        "missing_job",
        "ambiguous_matrix",
    ],
)
def test_support_contract_rejects_missing_targets_or_relaxed_gates(mutation):
    project, workflow = copy.deepcopy(_valid_inputs())
    classifier_versions = {
        "missing_old_classifier": "3.10",
        "missing_314_classifier": "3.14",
        "missing_315_classifier": "3.15",
    }
    if mutation in classifier_versions:
        project["classifiers"].remove("Programming Language :: Python :: " + classifier_versions[mutation])
    elif mutation == "duplicate_classifier":
        project["classifiers"].append(project["classifiers"][-1])
    elif mutation in {"higher_minimum", "upper_bound"}:
        project["requires-python"] = ">=3.11" if mutation == "higher_minimum" else ">=3.10,<3.15"
    elif mutation.startswith("missing_") and mutation.endswith("_matrix"):
        removed = {"missing_old_matrix": "3.10", "missing_314_matrix": "3.14", "missing_315_matrix": "3.15"}[mutation]
        workflow = workflow.replace(json.dumps(VERSIONS), json.dumps([v for v in VERSIONS if v != removed]))
    elif mutation == "duplicate_matrix":
        workflow = workflow.replace(json.dumps(VERSIONS), json.dumps([*VERSIONS, "3.15"]))
    elif mutation in {"missing_windows", "missing_ubuntu"}:
        retained = ["ubuntu-latest"] if mutation == "missing_windows" else ["windows-latest"]
        workflow = workflow.replace(json.dumps(PLATFORMS), json.dumps(retained))
    elif mutation == "continue_on_error":
        workflow = workflow.replace("    strategy:", "    continue-on-error: true\n    strategy:")
    elif mutation == "warnings_relaxed":
        workflow = workflow.replace(" -W error", "")
    elif mutation == "branch_gate_removed":
        workflow = workflow.replace(" --cov-branch", "")
    elif mutation == "interpreter_not_bound":
        workflow = workflow.replace("${{ matrix.python-version }}", '"3.12"')
    elif mutation == "missing_job":
        workflow = workflow.replace("  test:", "  other:")
    elif mutation == "ambiguous_matrix":
        workflow = workflow.replace("        python-version:", "        python-version: []\n        python-version:")
    with pytest.raises(AssertionError):
        _assert_support_contract(project, workflow)
