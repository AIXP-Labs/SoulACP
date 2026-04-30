"""CLI binary discovery utilities."""

from __future__ import annotations

import os
import shutil
import sys


def find_binary(names: list[str]) -> str | None:
    """Find a CLI binary by searching PATH and common install locations.

    Args:
        names: Binary names to search for, in priority order.

    Returns:
        Absolute path to the binary, or ``None`` if not found.
    """
    for name in names:
        cmd = shutil.which(name)
        if cmd:
            return cmd
        # Windows: check npm global install locations
        if sys.platform == "win32":
            for base_var in ("APPDATA", "LOCALAPPDATA"):
                base = os.environ.get(base_var, "")
                if base:
                    for suffix in (".cmd", ".exe", ""):
                        path = os.path.join(base, "npm", f"{name}{suffix}")
                        if os.path.exists(path):
                            return path
    return None


def find_claude_binary() -> str | None:
    """Find the Claude CLI binary (claude-code-acp or claude)."""
    return find_binary(["claude-code-acp", "claude"])


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
    """Find the Cursor CLI binary (cursor-agent or agent)."""
    return find_binary(["cursor-agent", "agent"])


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
