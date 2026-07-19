"""CLI binary discovery utilities."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath

from soulacp.environment import get_subprocess_environment_value

_WINDOWS_DEFAULT_PATHEXT = (".COM", ".EXE", ".BAT", ".CMD")
_WINDOWS_EXTENSION = re.compile(r"^\.[A-Za-z0-9]+$")


def _environment_value(environment: Mapping[str, str] | None, name: str, default: str = "") -> str:
    source = os.environ if environment is None else environment
    value = get_subprocess_environment_value(source, name, default)
    return default if value is None else value


def _candidate_names(name: str, environment: Mapping[str, str] | None) -> list[str]:
    if sys.platform != "win32":
        return [name]
    extensions = [
        extension
        for raw_extension in _environment_value(environment, "PATHEXT").split(";")
        if (extension := raw_extension.strip()) and _WINDOWS_EXTENSION.fullmatch(extension)
    ]
    if not extensions:
        extensions = list(_WINDOWS_DEFAULT_PATHEXT)
    if any(name.lower().endswith(extension.lower()) for extension in extensions):
        return [name]
    return [f"{name}{extension}" for extension in extensions]


def _is_bare_binary_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and "\x00" not in name
        and PurePosixPath(name).name == name
        and PureWindowsPath(name).name == name
    )


def _find_on_absolute_path(name: str, environment: Mapping[str, str] | None = None) -> str | None:
    """Resolve *name* without trusting implicit current-directory entries."""
    if not _is_bare_binary_name(name):
        return None

    if environment is None:
        directories = os.get_exec_path()
    else:
        # ``os.get_exec_path(mapping)`` performs a case-sensitive ``PATH``
        # lookup even on Windows. Child environment blocks are
        # case-insensitive there, so honor common spellings such as ``Path``.
        search_path = _environment_value(environment, "PATH", os.defpath)
        directories = search_path.split(os.pathsep)
    for raw_directory in directories:
        directory = raw_directory.strip('"')
        if not directory or not os.path.isabs(directory):
            continue
        for candidate in _candidate_names(name, environment):
            path = os.path.join(directory, candidate)
            if os.path.isfile(path) and (sys.platform == "win32" or os.access(path, os.X_OK)):
                return os.path.abspath(path)
    return None


def find_binary_in_environment(
    names: list[str],
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Find a CLI binary by searching PATH and common install locations.

    Args:
        names: Binary names to search for, in priority order.

    Returns:
        Absolute path to the binary, or ``None`` if not found.
    """
    for name in names:
        if not _is_bare_binary_name(name):
            continue
        cmd = _find_on_absolute_path(name, environment)
        if cmd:
            return cmd
        # Windows: check npm global install locations
        if sys.platform == "win32":
            for base_var in ("APPDATA", "LOCALAPPDATA"):
                base = _environment_value(environment, base_var)
                if base and os.path.isabs(base):
                    for suffix in (".cmd", ".exe", ""):
                        path = os.path.join(base, "npm", f"{name}{suffix}")
                        if os.path.isfile(path):
                            return os.path.abspath(path)
    return None


def find_binary(names: list[str]) -> str | None:
    """Find a CLI binary in the current process environment."""
    return find_binary_in_environment(names)


def find_claude_binary() -> str | None:
    """Find a Claude ACP bridge, preferring the current package name."""
    return find_binary(["claude-agent-acp", "claude-code-acp"])


def find_gemini_binary() -> str | None:
    """Find the Gemini CLI binary."""
    return find_binary(["gemini"])


def find_opencode_binary() -> str | None:
    """Find the OpenCode CLI binary."""
    return find_binary(["opencode"])


def find_openclaw_binary() -> str | None:
    """Find the OpenClaw CLI binary."""
    return find_binary(["openclaw"])


def find_cursor_binary() -> str | None:
    """Find the official Cursor CLI binary."""
    return find_binary(["cursor-agent"])


def find_codex_binary() -> str | None:
    """Find the codex-acp binary."""
    return find_binary(["codex-acp"])


def find_qwen_binary() -> str | None:
    """Find the Qwen Code CLI binary."""
    return find_binary(["qwen-code", "qwen"])


def find_kimi_binary() -> str | None:
    """Find the Kimi CLI binary."""
    return find_binary(["kimi"])


def find_codebuddy_binary() -> str | None:
    """Find the Codebuddy Code CLI binary."""
    return find_binary(["codebuddy-code"])


def find_cline_binary() -> str | None:
    """Find the Cline CLI binary."""
    return find_binary(["cline"])


def find_copilot_binary() -> str | None:
    """Find the GitHub Copilot CLI binary."""
    return find_binary(["copilot"])


def find_minion_binary() -> str | None:
    """Find the Minion Code CLI binary."""
    return find_binary(["minion-code"])


def find_vibe_binary() -> str | None:
    """Find the Mistral Vibe ACP binary."""
    return find_binary(["vibe-acp"])


def find_nova_binary() -> str | None:
    """Find the Nova CLI binary."""
    return find_binary(["nova"])


def find_crow_binary() -> str | None:
    """Find the Crow CLI binary."""
    return find_binary(["crow-cli"])


def find_amp_binary() -> str | None:
    """Find the Amp ACP binary."""
    return find_binary(["amp-acp"])


def find_auggie_binary() -> str | None:
    """Find the Auggie CLI binary."""
    return find_binary(["auggie"])


def find_autohand_binary() -> str | None:
    """Find the Autohand ACP binary."""
    return find_binary(["autohand-acp"])


def find_corust_binary() -> str | None:
    """Find the Corust Agent ACP binary."""
    return find_binary(["corust-agent-acp"])


def find_deepagents_binary() -> str | None:
    """Find the DeepAgents ACP binary."""
    return find_binary(["deepagents-acp"])


def find_droid_binary() -> str | None:
    """Find the Factory Droid CLI binary."""
    return find_binary(["droid"])


def find_fastagent_binary() -> str | None:
    """Find the fast-agent ACP binary."""
    return find_binary(["fast-agent-acp"])


def find_copilot_ls_binary() -> str | None:
    """Find the GitHub Copilot Language Server binary."""
    return find_binary(["copilot-language-server"])


def find_goose_binary() -> str | None:
    """Find the Goose CLI binary."""
    return find_binary(["goose"])


def find_junie_binary() -> str | None:
    """Find the Junie CLI binary."""
    return find_binary(["junie"])


def find_kilo_binary() -> str | None:
    """Find the Kilo CLI binary."""
    return find_binary(["kilo"])


def find_pi_binary() -> str | None:
    """Find the pi ACP binary."""
    return find_binary(["pi-acp"])


def find_qoder_binary() -> str | None:
    """Find the Qoder CLI binary."""
    return find_binary(["qodercli"])


def find_stakpak_binary() -> str | None:
    """Find the Stakpak CLI binary."""
    return find_binary(["stakpak"])
