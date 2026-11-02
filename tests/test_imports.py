"""Test that all soulacp modules import correctly."""

import re
from pathlib import Path

import pytest
import tomli


def _project_metadata() -> dict:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as stream:
        return tomli.load(stream)["project"]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _assert_changelog_release_structure(changelog: str, version: str) -> None:
    pending_heading = "## [Unreleased]"
    release_heading = f"## [{version}]"
    assert changelog.count(pending_heading) == 1
    assert changelog.count(release_heading) == 1
    _, _, after_pending = changelog.partition(pending_heading + "\n")
    pending, separator, _ = after_pending.partition(release_heading)
    assert separator, "current source version must follow Unreleased"
    assert "## [" not in pending, "another release precedes the current source version"
    pending = pending.strip()
    assert pending, "Unreleased must contain a placeholder or change entries"
    if pending == "No changes yet.":
        return
    assert "No changes yet." not in pending, "empty placeholder conflicts with pending changes"
    sections = re.split(r"(?m)^### [^\n]+\n", pending + "\n")
    assert len(sections) > 1, "pending changes require named sections"
    assert all(re.search(r"(?m)^- \S", body) for body in sections[1:]), "pending change section is empty"


@pytest.mark.parametrize(
    "pending", ["No changes yet.", "### Fixed\n- Correct test ownership.\n\n### Documentation\n- Explain scope."]
)
def test_changelog_structure_accepts_empty_or_documented_pending_changes(pending):
    _assert_changelog_release_structure(
        f"# Changelog\n\n## [Unreleased]\n\n{pending}\n\n## [0.2.4] - Unreleased\n", "0.2.4"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_pending",
        "duplicate_pending",
        "missing_current",
        "duplicate_current",
        "reversed",
        "empty_pending",
        "stale_placeholder",
        "unnamed_changes",
        "empty_section",
        "intervening_release",
    ],
)
def test_changelog_structure_rejects_missing_conflicting_or_reordered_entries(mutation):
    document = "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- Correct test ownership.\n\n## [0.2.4] - Unreleased\n"
    changes = {
        "missing_pending": document.replace("## [Unreleased]\n", ""),
        "duplicate_pending": "## [Unreleased]\n" + document,
        "missing_current": document.replace("## [0.2.4]", "## [0.2.3]"),
        "duplicate_current": document + "\n## [0.2.4]\n",
        "reversed": "## [0.2.4]\n\n## [Unreleased]\n\n### Fixed\n- Item.\n",
        "empty_pending": document.replace("### Fixed\n- Correct test ownership.\n", ""),
        "stale_placeholder": document.replace("### Fixed", "No changes yet.\n\n### Fixed"),
        "unnamed_changes": document.replace("### Fixed\n- Correct test ownership.", "Pending changes."),
        "empty_section": document.replace("### Fixed", "### Documentation\n\n### Fixed"),
        "intervening_release": document.replace("## [0.2.4]", "## [0.2.3]\n\n## [0.2.4]"),
    }
    with pytest.raises(AssertionError):
        _assert_changelog_release_structure(changes[mutation], "0.2.4")


def test_package_import():
    import re

    import soulacp

    # Just verify the version is a valid SemVer string, not a hardcoded value
    # (avoid stale assertions when the package version bumps).
    assert re.match(r"^\d+\.\d+\.\d+", soulacp.__version__), f"unexpected version format: {soulacp.__version__!r}"
    # Verify key exports exist rather than hardcoding count
    expected = {
        "ManagedSession",
        "ACPConfig",
        "ACPConnectionPool",
        "ACPClient",
        "ACPClientBase",
        "is_context_overflow",
    }
    assert expected.issubset(set(soulacp.__all__))
    assert callable(soulacp.is_context_overflow)
    assert _project_metadata()["version"] == soulacp.__version__


def test_release_version_is_consistent_across_owned_metadata():
    import soulacp

    root = _project_root()
    version = soulacp.__version__

    with (root / "uv.lock").open("rb") as stream:
        lock = tomli.load(stream)
    locked_versions = [package["version"] for package in lock["package"] if package.get("name") == "soulacp"]
    assert locked_versions == [version]

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    _assert_changelog_release_structure(changelog, version)

    footer_docs = (
        "README.md",
        "README_CN.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "CONTRIBUTING_CN.md",
        "GOVERNANCE.md",
        "SECURITY.md",
    )
    expected_footer = f"Version: SoulACP V{version}."
    for filename in footer_docs:
        content = (root / filename).read_text(encoding="utf-8")
        assert expected_footer in content, f"stale or missing version footer in {filename}"


def test_config_import():
    from soulacp.config import ACPConfig

    config = ACPConfig(provider="claude", model="claude-acp/sonnet")
    assert config.provider == "claude"
    assert config.pool_size == 5
    assert config.pool_max_connections == 8
    assert config.timeout_connect == 30
    assert config.max_retries == 3


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("ACP_PROVIDER", "gemini-acp")
    monkeypatch.setenv("ACP_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("ACP_POOL_SIZE", "5")
    from soulacp.config import ACPConfig

    config = ACPConfig.from_env()
    assert config.provider == "gemini-acp"
    assert config.model == "gemini-2.5-flash"
    assert config.pool_size == 5


def test_resolve_provider():
    from soulacp.config import resolve_provider

    assert resolve_provider("claude-acp/claude-sonnet-4-20250514") == "claude"
    assert resolve_provider("gemini-acp/gemini-2.5-flash") == "gemini"


def test_legacy_protocol_module_reexports_canonical_protocol():
    from soulacp.adapters.protocol import ACPClient
    from soulacp.protocol import ACPClientProtocol

    assert ACPClientProtocol is ACPClient


def test_all_registered_clients_satisfy_public_protocol():
    from soulacp import ACPClient, ACPConfig
    from soulacp.config import resolve_client_class
    from soulacp.registry import list_agents

    for agent in list_agents():
        client_class = resolve_client_class(agent.name)
        client = client_class(ACPConfig(provider=agent.name, model=f"{agent.model_prefix}default"))
        assert isinstance(client, ACPClient), f"{client_class.__name__} does not satisfy ACPClient"


def test_github_actions_are_pinned_to_immutable_commits():
    import re

    workflows = _project_root() / ".github" / "workflows"
    references: list[tuple[Path, str]] = []
    for workflow in workflows.glob("*.yml"):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            match = re.search(r"\buses:\s*\S+@([^\s#]+)", line)
            if match:
                references.append((workflow, match.group(1)))

    assert references, "no GitHub Actions references found"
    for workflow, reference in references:
        assert re.fullmatch(r"[0-9a-f]{40}", reference), (
            f"{workflow.name} action reference is not an immutable commit: {reference}"
        )


def test_pre_commit_repositories_are_pinned_to_immutable_commits():
    import re

    config = (_project_root() / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    references = re.findall(r"^\s*rev:\s*([^\s#]+)", config, re.MULTILINE)

    assert references, "no pre-commit repository references found"
    assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for reference in references)


def test_development_requirement_manifests_are_synchronized():
    project_dev = _project_metadata()["optional-dependencies"]["dev"]
    requirement_lines = [
        line
        for line in (_project_root() / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    minimum_lines = [
        line
        for line in (_project_root() / "requirements-min.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]

    assert requirement_lines == project_dev
    assert all(">=" in requirement for requirement in project_dev)
    assert all("==" in requirement for requirement in minimum_lines)
    assert [requirement.split(">=", 1)[0] for requirement in project_dev] == [
        requirement.split("==", 1)[0] for requirement in minimum_lines
    ]


def test_resolve_client_class():
    from soulacp.adapters.claude_client import ClaudeACPClient
    from soulacp.adapters.gemini_client import GeminiACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("claude") is ClaudeACPClient
    assert resolve_client_class("gemini") is GeminiACPClient


def test_adapters_import():
    from soulacp.adapters import (
        ACPClientBase,
        AmpACPClient,
        AuggieACPClient,
        AutohandACPClient,
        ClaudeACPClient,
        ClineACPClient,
        CodebuddyACPClient,
        CodexACPClient,
        CopilotACPClient,
        CopilotLSACPClient,
        CorustACPClient,
        CrowACPClient,
        CursorACPClient,
        CursorCLIClient,
        DeepAgentsACPClient,
        DroidACPClient,
        FastAgentACPClient,
        GeminiACPClient,
        GooseACPClient,
        JunieACPClient,
        KiloACPClient,
        KimiACPClient,
        MinionACPClient,
        NovaACPClient,
        OpenClawACPClient,
        OpenCodeACPClient,
        PiACPClient,
        QoderACPClient,
        QwenACPClient,
        StakpakACPClient,
        VibeACPClient,
    )

    assert ACPClientBase is not None
    assert ClaudeACPClient is not None
    assert GeminiACPClient is not None
    assert OpenCodeACPClient is not None
    assert OpenClawACPClient is not None
    assert CursorACPClient is not None
    assert CursorCLIClient is not None
    assert CodexACPClient is not None
    assert QwenACPClient is not None
    assert KimiACPClient is not None
    assert CodebuddyACPClient is not None
    assert ClineACPClient is not None
    assert CopilotACPClient is not None
    assert MinionACPClient is not None
    assert VibeACPClient is not None
    assert NovaACPClient is not None
    assert CrowACPClient is not None
    assert AmpACPClient is not None
    assert AuggieACPClient is not None
    assert AutohandACPClient is not None
    assert CorustACPClient is not None
    assert CopilotLSACPClient is not None
    assert DeepAgentsACPClient is not None
    assert DroidACPClient is not None
    assert FastAgentACPClient is not None
    assert GooseACPClient is not None
    assert JunieACPClient is not None
    assert KiloACPClient is not None
    assert PiACPClient is not None
    assert QoderACPClient is not None
    assert StakpakACPClient is not None


def test_pool_import():
    from soulacp.pool import ACPConnectionPool

    assert ACPConnectionPool is not None


def test_retry_import():
    from soulacp.retry import is_retryable, retry_async

    assert callable(retry_async)
    assert callable(is_retryable)


def test_binary_import():
    from soulacp.binary import (
        find_amp_binary,
        find_auggie_binary,
        find_autohand_binary,
        find_binary,
        find_claude_binary,
        find_cline_binary,
        find_codebuddy_binary,
        find_codex_binary,
        find_copilot_binary,
        find_copilot_ls_binary,
        find_corust_binary,
        find_crow_binary,
        find_cursor_binary,
        find_deepagents_binary,
        find_droid_binary,
        find_fastagent_binary,
        find_gemini_binary,
        find_goose_binary,
        find_junie_binary,
        find_kilo_binary,
        find_kimi_binary,
        find_minion_binary,
        find_nova_binary,
        find_openclaw_binary,
        find_opencode_binary,
        find_pi_binary,
        find_qoder_binary,
        find_qwen_binary,
        find_stakpak_binary,
        find_vibe_binary,
    )

    assert callable(find_binary)
    assert callable(find_claude_binary)
    assert callable(find_gemini_binary)
    assert callable(find_opencode_binary)
    assert callable(find_openclaw_binary)
    assert callable(find_cursor_binary)
    assert callable(find_codex_binary)
    assert callable(find_qwen_binary)
    assert callable(find_kimi_binary)
    assert callable(find_codebuddy_binary)
    assert callable(find_cline_binary)
    assert callable(find_copilot_binary)
    assert callable(find_minion_binary)
    assert callable(find_vibe_binary)
    assert callable(find_nova_binary)
    assert callable(find_crow_binary)
    assert callable(find_amp_binary)
    assert callable(find_auggie_binary)
    assert callable(find_autohand_binary)
    assert callable(find_corust_binary)
    assert callable(find_copilot_ls_binary)
    assert callable(find_deepagents_binary)
    assert callable(find_droid_binary)
    assert callable(find_fastagent_binary)
    assert callable(find_goose_binary)
    assert callable(find_junie_binary)
    assert callable(find_kilo_binary)
    assert callable(find_pi_binary)
    assert callable(find_qoder_binary)
    assert callable(find_stakpak_binary)


def test_services_import():
    from soulacp.services import FSService, TerminalService, resolve_path

    assert FSService is not None
    assert TerminalService is not None
    assert callable(resolve_path)


def test_no_external_deps():
    """Verify the core distribution declares no runtime dependencies."""
    assert _project_metadata().get("dependencies", []) == []
