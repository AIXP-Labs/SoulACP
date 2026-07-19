"""Test binary discovery."""

import os

import pytest


def test_find_binary_existing():
    from soulacp.binary import find_binary

    # python should exist on any system
    result = find_binary(["python3", "python"])
    assert result is not None
    assert os.path.isabs(result)


def test_find_binary_nonexistent():
    from soulacp.binary import find_binary

    result = find_binary(["nonexistent_binary_xyz_123"])
    assert result is None


def test_find_claude_binary():
    from soulacp.binary import find_claude_binary

    # May or may not be installed, just verify it doesn't crash
    result = find_claude_binary()
    # result is str or None, both are valid
    assert result is None or isinstance(result, str)


def test_find_claude_binary_prefers_current_acp(monkeypatch):
    from soulacp import binary

    calls = []
    monkeypatch.setattr(binary, "find_binary", lambda names: calls.append(names) or "claude-agent-acp")

    assert binary.find_claude_binary() == "claude-agent-acp"
    assert calls == [["claude-agent-acp", "claude-code-acp"]]


def test_find_gemini_binary():
    from soulacp.binary import find_gemini_binary

    result = find_gemini_binary()
    assert result is None or isinstance(result, str)


def test_find_cursor_binary_uses_unambiguous_official_name(monkeypatch):
    from soulacp import binary

    calls = []
    monkeypatch.setattr(binary, "find_binary", lambda names: calls.append(names) or "cursor-agent")

    assert binary.find_cursor_binary() == "cursor-agent"
    assert calls == [["cursor-agent"]]


def test_windows_npm_fallback_ignores_relative_appdata(monkeypatch, tmp_path):
    from soulacp import binary

    monkeypatch.chdir(tmp_path)
    npm_dir = tmp_path / "relative-appdata" / "npm"
    npm_dir.mkdir(parents=True)
    expected = npm_dir / "fake-cli.cmd"
    expected.write_text("", encoding="utf-8")
    monkeypatch.setattr(binary.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "relative-appdata")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert binary.find_binary(["fake-cli"]) is None


def test_find_binary_ignores_relative_and_empty_path_entries(monkeypatch, tmp_path):
    from soulacp import binary

    local_binary = tmp_path / "fake-cli"
    local_binary.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(binary.os, "get_exec_path", lambda: ["", "."])

    assert binary.find_binary(["fake-cli"]) is None


def test_find_binary_searches_explicit_absolute_path_entries(monkeypatch, tmp_path):
    from soulacp import binary

    trusted = tmp_path / "trusted"
    trusted.mkdir()
    expected = trusted / ("fake-cli.EXE" if binary.sys.platform == "win32" else "fake-cli")
    expected.write_text("", encoding="utf-8")
    expected.chmod(0o755)
    monkeypatch.setattr(binary.os, "get_exec_path", lambda: [str(trusted)])

    assert binary.find_binary(["fake-cli"]) == str(expected)


def test_find_binary_in_environment_uses_the_supplied_path(monkeypatch, tmp_path):
    from soulacp import binary

    trusted = tmp_path / "trusted"
    trusted.mkdir()
    expected = trusted / ("fake-cli.EXE" if binary.sys.platform == "win32" else "fake-cli")
    expected.write_text("", encoding="utf-8")
    expected.chmod(0o755)
    environment = {"PATH": str(trusted)}

    assert binary.find_binary_in_environment(["fake-cli"], environment) == str(expected)


def test_windows_path_search_expands_pathext_without_current_directory(monkeypatch, tmp_path):
    from soulacp import binary

    trusted = tmp_path / "trusted"
    trusted.mkdir()
    expected = trusted / "fake-cli.EXE"
    expected.write_text("", encoding="utf-8")
    environment = {"PATH": str(trusted), "PathExt": ".EXE;.CMD"}
    monkeypatch.setattr(binary.sys, "platform", "win32")

    assert binary.find_binary_in_environment(["fake-cli"], environment) == str(expected)


@pytest.mark.skipif(os.name != "nt", reason="Windows environment names are case-insensitive")
def test_windows_path_search_honors_case_insensitive_path_name(monkeypatch, tmp_path):
    from soulacp import binary

    trusted = tmp_path / "trusted"
    trusted.mkdir()
    expected = trusted / "fake-cli.EXE"
    expected.write_text("", encoding="utf-8")
    environment = {"Path": str(trusted), "PathExt": ".EXE"}
    monkeypatch.setattr(binary.sys, "platform", "win32")

    assert binary.find_binary_in_environment(["fake-cli"], environment) == str(expected)


def test_windows_pathext_rejects_path_components_and_uses_safe_defaults(monkeypatch):
    from soulacp import binary

    monkeypatch.setattr(binary.sys, "platform", "win32")

    assert binary._candidate_names("fake-cli", {"PATHEXT": r"..\evil;.CMD/..;C:.EXE"}) == [
        "fake-cli.COM",
        "fake-cli.EXE",
        "fake-cli.BAT",
        "fake-cli.CMD",
    ]


def test_windows_npm_fallback_rejects_non_bare_names(monkeypatch, tmp_path):
    from soulacp import binary

    escaped = tmp_path / "escaped.cmd"
    escaped.write_text("", encoding="utf-8")
    monkeypatch.setattr(binary.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert binary.find_binary(["../escaped"]) is None
    assert binary.find_binary([r"..\escaped"]) is None


def test_find_binary_ignores_windows_npm_directory(monkeypatch, tmp_path):
    from soulacp import binary

    npm_dir = tmp_path / "npm"
    (npm_dir / "fake-cli.cmd").mkdir(parents=True)
    monkeypatch.setattr(binary.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert binary.find_binary(["fake-cli"]) is None
