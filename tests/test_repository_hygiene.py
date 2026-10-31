"""Repository-only ignore rules and source distribution completeness."""

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    path = ROOT / "tools" / f"{name}.py"
    assert path.is_file(), f"missing source-distribution tool: {name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "path,ignored",
    [
        ("sessions.json", True),
        ("examples/sessions.json", True),
        (".venv-hooks/pyvenv.cfg", True),
        (".venv-hooks/Scripts/activate", True),
        (".pypirc", True),
        ("auth.json", True),
        (".codex/auth.json", True),
        (".claude/settings.local.json", True),
        ("tests/fixtures/sessions.json", False),
        ("tests/fixtures/public.pem", False),
        (".claude/settings.json", False),
        ("requirements-hooks.txt", False),
    ],
)
def test_ignore_rules_are_portable_and_do_not_hide_public_inputs(tmp_path, path, ignored):
    git = shutil.which("git")
    assert git is not None, "Git is required for repository development checks"
    (tmp_path / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run([git, "init", "--quiet"], cwd=tmp_path, env=env, check=True, capture_output=True)
    result = subprocess.run(
        [git, "check-ignore", "--no-index", "--stdin", "-z"],
        input=path.encode() + b"\0",
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )
    assert result.returncode in {0, 1}
    assert (result.returncode == 0) is ignored


def test_manifest_includes_all_new_hook_inputs():
    lines = (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "include requirements-hooks.txt" in lines
    assert "recursive-include tools *.py" in lines


@pytest.mark.parametrize("missing", [None, "requirements-hooks.txt", "tools/run_hooks.py"])
def test_sdist_check_rejects_missing_files(tmp_path, missing):
    checker = load_tool("check_sdist")
    path = tmp_path / "synthetic.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name in sorted(checker.REQUIRED_INPUTS - {missing}):
            entry = tarfile.TarInfo("synthetic/" + name)
            entry.size = 1
            archive.addfile(entry, io.BytesIO(b"x"))
    if missing:
        with pytest.raises(ValueError, match="missing required inputs"):
            checker.validate_sdist(path)
    else:
        checker.validate_sdist(path)


def test_sdist_check_rejects_unsafe_archive_names(tmp_path):
    checker = load_tool("check_sdist")
    path = tmp_path / "synthetic.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        entry = tarfile.TarInfo("../synthetic.txt")
        archive.addfile(entry, io.BytesIO())
    with pytest.raises(ValueError, match="unsafe"):
        checker.validate_sdist(path)


def test_hook_launcher_does_not_fall_back_to_path(tmp_path, monkeypatch):
    runner = load_tool("run_hooks")
    calls = []
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    assert runner.main(["--python", str(tmp_path / "missing")]) == 2
    assert calls == []


def test_hook_launcher_binds_one_explicit_interpreter_and_preserves_status(tmp_path, monkeypatch):
    runner = load_tool("run_hooks")
    python = tmp_path / "hook tools" / "python"
    python.parent.mkdir()
    python.touch()
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main(["--python", str(python)]) == 7
    assert len(calls) == 1
    assert calls[0][0][0] == str(python.resolve())
    assert calls[0][0][1] == "-c"
    assert calls[0][1].get("shell", False) is False


def test_hook_launcher_refuses_python310_without_running_hooks(tmp_path):
    runner = load_tool("run_hooks")
    probe = "import sys; sys.version_info = (3, 10, 0); " + runner.HOOK_ENTRY
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "Python >=3.11" in result.stderr


def test_make_hooks_uses_explicit_python_wrapper():
    content = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "HOOKS_PYTHON ?=" in content
    assert 'tools/run_hooks.py --python "$(HOOKS_PYTHON)"' in content
    hook_recipe = content.split("\nhooks:\n", 1)[1].split("\n\n", 1)[0]
    assert "\tpre-commit " not in hook_recipe
