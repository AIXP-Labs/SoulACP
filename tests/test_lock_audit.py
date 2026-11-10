"""All-marker audits must cover each locked version and fail closed on gaps."""

import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lock_audit", ROOT / "tools/audit_lock.py")
assert SPEC is not None and SPEC.loader is not None
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)
PROJECT = {"name": "soulacp", "version": "0.2.6"}


def registry(name, version, **values):
    return {"name": name, "version": version, "source": {"registry": "https://pypi.org/simple"}, **values}


def lock(*packages):
    return {"package": [{**PROJECT, "source": {"editable": "."}}, *packages]}


def response(pairs, vulnerabilities=None):
    return json.dumps({"dependencies": [{"name": n, "version": v, "vulns": vulnerabilities or []} for n, v in pairs]})


def test_inactive_python_and_windows_marker_versions_are_all_audited(monkeypatch):
    packages = TOOL.locked_packages(
        lock(
            registry("alternate", "1.0", **{"resolution-markers": ["python_full_version < '3.11'"]}),
            registry("alternate", "2.0", **{"resolution-markers": ["python_full_version >= '3.11'"]}),
            registry("windows-only", "3.0", **{"resolution-markers": ["sys_platform == 'win32'"]}),
        ),
        PROJECT,
    )
    calls = []

    def run(command, **kwargs):
        assert command[:5] == [TOOL.sys.executable, "-I", "-B", "-m", "pip_audit"]
        assert {"--no-deps", "--disable-pip", "--strict"} <= set(command)
        assert command[command.index("--vulnerability-service") + 1] == "pypi"
        assert not {"--fix", "--ignore-vuln", "--skip-editable"} & set(command)
        requirements = Path(command[command.index("--requirement") + 1]).read_text(encoding="utf-8")
        assert ";" not in requirements
        pairs = [tuple(line.split("==")) for line in requirements.splitlines()]
        assert len({name for name, _ in pairs}) == len(pairs)
        assert kwargs["capture_output"] is True and kwargs["check"] is False
        calls.extend(pairs)
        return SimpleNamespace(returncode=0, stdout=response(reversed(pairs)))

    monkeypatch.setattr(TOOL.subprocess, "run", run)
    result = TOOL.audit(packages)
    assert sorted(calls) == packages
    assert result["status"] == "PASS" and result["audited_package_versions"] == 3
    assert result["batches"] == 2 and result["skipped_dependencies"] == 0


def test_inherited_output_cannot_redirect_report_or_touch_user_cache(monkeypatch, tmp_path):
    target = tmp_path / "preserved-report.txt"
    target.write_text("preserved", encoding="utf-8")
    monkeypatch.setenv("PIP_AUDIT_OUTPUT", str(target))
    temporary_caches = []

    def run(command, **_kwargs):
        # pip-audit otherwise uses its environment output and may migrate a user cache.
        assert command[command.index("--output") + 1] == "stdout"
        cache = Path(command[command.index("--cache-dir") + 1])
        requirements = Path(command[command.index("--requirement") + 1])
        assert cache.parent == requirements.parent
        assert cache.parent != tmp_path
        cache.mkdir(exist_ok=True)
        (cache / "synthetic-cache-entry").write_text("temporary", encoding="utf-8")
        temporary_caches.append(cache)
        return SimpleNamespace(returncode=0, stdout=response([("alpha", "1.0")]))

    monkeypatch.setattr(TOOL.subprocess, "run", run)
    assert TOOL.audit([("alpha", "1.0")])["status"] == "PASS"
    assert target.read_text(encoding="utf-8") == "preserved"
    assert temporary_caches and all(not cache.exists() for cache in temporary_caches)


@pytest.mark.parametrize(
    "source",
    [
        {"registry": "https://synthetic-user:synthetic-private-value@invalid.example/simple"},
        {"git": "https://invalid.example/synthetic-private-value"},
        {"path": "synthetic-private-value"},
    ],
)
def test_unknown_or_private_sources_cannot_be_silently_skipped_or_disclosed(source):
    with pytest.raises(TOOL.AuditError) as caught:
        TOOL.locked_packages(lock({"name": "private", "version": "1.0", "source": source}), PROJECT)
    assert "synthetic-private-value" not in str(caught.value)


@pytest.mark.parametrize("value", [None, [], {}, {"package": []}])
def test_empty_or_malformed_lock_cannot_pass(value):
    with pytest.raises(TOOL.AuditError):
        TOOL.locked_packages(value, PROJECT)


@pytest.mark.parametrize("field,value", [("name", "alpha\n--index-url"), ("version", "1.0\nmalicious==1")])
def test_requirement_input_cannot_inject_options(field, value):
    package = registry("alpha", "1.0")
    package[field] = value
    with pytest.raises(TOOL.AuditError):
        TOOL.locked_packages(lock(package), PROJECT)


def test_only_the_exact_current_editable_project_is_excluded():
    data = lock(registry("alpha", "1.0"))
    data["package"][0]["version"] = "0.0.1"
    with pytest.raises(TOOL.AuditError):
        TOOL.locked_packages(data, PROJECT)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "skip", "no_vulns", "wrong_version"])
def test_incomplete_or_ambiguous_audit_results_cannot_pass(mutation):
    expected = [("alpha", "1.0"), ("beta", "2.0")]
    data = json.loads(response(expected))
    rows = data["dependencies"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(rows[0].copy())
    elif mutation == "extra":
        rows.append({"name": "extra", "version": "1.0", "vulns": []})
    elif mutation == "skip":
        rows[0]["skip_reason"] = "synthetic-private-server-error"
    elif mutation == "no_vulns":
        rows[0].pop("vulns")
    else:
        rows[0]["version"] = "9.0"
    with pytest.raises(TOOL.AuditError) as caught:
        TOOL.validate_response(json.dumps(data), expected, 0)
    assert "synthetic-private-server-error" not in str(caught.value)


@pytest.mark.parametrize("payload", ["not-json", "[]", '{"dependencies":[],"dependencies":[]}'])
def test_malformed_or_duplicate_json_does_not_pass(payload):
    with pytest.raises(TOOL.AuditError):
        TOOL.validate_response(payload, [("alpha", "1.0")], 0)


@pytest.mark.parametrize("code", [1, 2, -1])
def test_command_failure_cannot_be_hidden_by_clean_json(code):
    expected = [("alpha", "1.0")]
    with pytest.raises(TOOL.AuditError):
        TOOL.validate_response(response(expected), expected, code)


@pytest.mark.parametrize("code", [0, 1])
def test_vulnerabilities_fail_the_gate_even_with_a_zero_tool_exit(monkeypatch, code):
    def run(*_args, **_kwargs):
        return SimpleNamespace(returncode=code, stdout=response([("alpha", "1.0")], [{"id": "synthetic"}]))

    monkeypatch.setattr(TOOL.subprocess, "run", run)
    result = TOOL.audit([("alpha", "1.0")])
    assert result["status"] == "FAIL"
    assert result["vulnerable_packages"] == [{"name": "alpha", "version": "1.0", "vulnerability_count": 1}]


def test_timeout_is_a_failure_without_raw_diagnostic_output(monkeypatch, tmp_path, capsys):
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname="soulacp"\nversion="0.2.6"\nsource={editable="."}\n'
        '[[package]]\nname="alpha"\nversion="1.0"\nsource={registry="https://pypi.org/simple"}\n'
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nname="soulacp"\nversion="0.2.6"\n')
    monkeypatch.setattr(TOOL, "ROOT", tmp_path)

    def run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("synthetic-private-command", 1)

    monkeypatch.setattr(TOOL.subprocess, "run", run)
    assert TOOL.main([]) == 1
    value = capsys.readouterr()
    assert "synthetic-private-command" not in value.out + value.err
    assert json.loads(value.out)["status"] == "ERROR"


def test_existing_report_is_not_overwritten_or_audited(monkeypatch, tmp_path):
    target = tmp_path / "existing.json"
    target.write_text("preserved")
    monkeypatch.setattr(TOOL, "audit", lambda _: pytest.fail("audit should not start"))
    assert TOOL.main(["--output", str(target)]) == 2
    assert target.read_text() == "preserved"


@pytest.mark.parametrize(
    "missing",
    [
        "tools/audit_lock.py",
        "tests/test_lock_audit.py",
        "tests/test_session_response_ordering.py",
        "tests/test_opencode_response_ordering.py",
        "tests/test_shutdown_exception_privacy.py",
    ],
)
def test_distribution_cannot_omit_critical_untracked_regression_inputs(tmp_path, missing):
    spec = importlib.util.spec_from_file_location("sdist_checker", ROOT / "tools/check_sdist.py")
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert missing in checker.REQUIRED_INPUTS
    archive_path = tmp_path / "synthetic.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name in sorted(checker.REQUIRED_INPUTS - {missing}):
            member = tarfile.TarInfo("synthetic/" + name)
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="missing required inputs"):
        checker.validate_sdist(archive_path)
