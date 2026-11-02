"""Non-destructive, same-interpreter release builds in fresh output directories."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def tool():
    path = ROOT / "tools/build_release.py"
    assert path.is_file()
    spec = importlib.util.spec_from_file_location("release_builder", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_make_build_does_not_depend_on_clean():
    import soulacp

    text = (ROOT / "Makefile").read_text()
    assert f"DIST_DIR ?= dist/{soulacp.__version__}" in text
    assert "\nbuild:\n" in text
    assert "build: clean" not in text
    assert 'tools/build_release.py --outdir "$(DIST_DIR)"' in text
    assert "\nclean:\n" in text


@pytest.mark.parametrize("kind", ["file", "empty-directory", "populated-directory"])
def test_existing_release_destination_is_never_overwritten(tmp_path, monkeypatch, kind):
    builder = tool()
    destination = tmp_path / "existing"
    if kind == "file":
        destination.write_bytes(b"synthetic-old-artifact")
    else:
        destination.mkdir()
        if kind == "populated-directory":
            (destination / "old.whl").write_bytes(b"synthetic-old-artifact")
    calls = []
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    assert builder.main(["--outdir", str(destination)]) == 2
    assert calls == []
    if kind == "file":
        assert destination.read_bytes() == b"synthetic-old-artifact"
    if kind == "populated-directory":
        assert (destination / "old.whl").read_bytes() == b"synthetic-old-artifact"


@pytest.mark.parametrize("failed_step", [None, "build", "twine", "inputs"])
def test_release_build_limits_outputs_and_preserves_failures(tmp_path, monkeypatch, failed_step):
    builder = tool()
    destination = tmp_path / "new output"
    old = tmp_path / "old.whl"
    old.write_bytes(b"keep")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[0] == sys.executable
        assert kwargs.get("shell", False) is False
        if "build" in command:
            assert command[-1] == str(destination)
            (destination / "synthetic.whl").write_bytes(b"wheel")
            (destination / "synthetic.tar.gz").write_bytes(b"source")
            step = "build"
        elif "twine" in command:
            assert old.as_posix() not in " ".join(command)
            step = "twine"
        else:
            assert str(builder.ROOT / "tools/check_sdist.py") in command
            step = "inputs"
        return subprocess.CompletedProcess(command, 7 if failed_step == step else 0)

    monkeypatch.setattr(builder.subprocess, "run", run)
    assert builder.main(["--outdir", str(destination)]) == (7 if failed_step else 0)
    assert old.read_bytes() == b"keep"
    assert destination.is_dir()
    assert len(calls) == ({"build": 1, "twine": 2, "inputs": 3}.get(failed_step, 3))


def test_fresh_build_without_expected_artifacts_is_not_success(tmp_path, monkeypatch):
    builder = tool()
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
    assert builder.main(["--outdir", str(tmp_path / "new")]) != 0
