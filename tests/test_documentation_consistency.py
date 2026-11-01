"""Offline documentation contracts, without provider calls or sibling checkouts."""

import ast
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

import pytest

import soulacp
from soulacp import ACPConfig

ROOT = Path(__file__).resolve().parents[1]
READMES = [ROOT / "README.md", ROOT / "README_CN.md"]
PROJECTS = (
    "HSAW",
    "AIZP",
    "AIIP",
    "AIKP",
    "AIJP",
    "AINP",
    "AILP",
    "AIVP",
    "AIRP",
    "AIBP",
    "AIAP",
    "AISP",
    "AISOP",
    "AIXPStore",
    "SoulSkill",
    "SoulAgent",
    "SoulBot",
    "SoulRun",
    "SoulACP",
)
QUALITY_COMMANDS = (
    "python -m pytest tests/ -W error -v --cov=soulacp --cov-branch --cov-report=term",
    "python -m ruff check src/soulacp/ tests/ examples/ tools/",
    "python -m ruff format --check src/soulacp/ tests/ examples/ tools/",
    "python tools/build_release.py --outdir dist/0.2.3",
    "python tools/check_sdist.py dist/0.2.3/soulacp-0.2.3.tar.gz",
)


def section(document, heading):
    _, separator, remaining = document.partition(heading + "\n")
    assert separator, "required documentation section missing"
    return re.split(r"(?m)^#{1,3} ", remaining, maxsplit=1)[0]


def table(document, heading):
    # These repository tables have no escaped pipes or multiline cells.
    rows = [
        line.strip().strip("|").split("|") for line in section(document, heading).splitlines() if line.startswith("|")
    ]
    assert len(rows) >= 2
    return [[cell.strip() for cell in row] for row in rows[2:]]


def project_identity(rows):
    result = []
    for row in rows:
        assert len(row) == 3
        match = re.fullmatch(r"\[([^]]+)\]\((https://[^)]+)\)", row[0])
        assert match is not None
        name, url = match.groups()
        assert url == "https://" + row[2]
        result.append(name)
    assert tuple(result) == PROJECTS, "project identity/order differs"


def quality_commands(document):
    assert all(command in document for command in QUALITY_COMMANDS), "quality command missing"
    assert "python -m build --outdir" not in document, "unguarded build example"


def technical_sections(document):
    without_fences = re.sub(r"(?ms)^```[^\n]*\n.*?^```", "", document)
    headings = list(re.finditer(r"(?m)^(#{1,6}) .+$", without_fences))
    result = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(without_fences)
        body = without_fences[heading.end() : end].strip()
        paragraphs = len(re.split(r"\n\s*\n", body)) if body else 0
        codes = Counter(re.findall(r"(?<!`)`([^`\r\n]+)`(?!`)", body))
        result.append((len(heading.group(1)), paragraphs, codes))
    return result


def executable_examples(document):
    result = []
    for language, body in re.findall(r"(?ms)^```([^\n]*)\n(.*?)^```", document):
        if language == "python":
            result.append((language, ast.dump(ast.parse(body), include_attributes=False)))
        elif language in {"bash", "powershell"}:
            commands = tuple(line for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#"))
            result.append((language, commands))
    return result


def test_bilingual_section_layout_and_technical_scope_match():
    en, cn = [p.read_text(encoding="utf-8") for p in READMES]
    assert technical_sections(en) == technical_sections(cn)


@pytest.mark.parametrize("mutation", ["move_scope", "split_paragraph"])
def test_section_alignment_detects_scope_and_layout_drift(mutation):
    en = "# Sample\n\n## Config\n\nKeep `field` private.\n\n## Privacy\n\nUse synthetic data.\n"
    cn = "# 示例\n\n## 配置\n\n保护`field`。\n\n## 隐私\n\n使用合成数据。\n"
    assert technical_sections(en) == technical_sections(cn)
    if mutation == "move_scope":
        cn = cn.replace("`field`", "").replace("使用合成数据。", "使用合成数据并保护`field`。")
    else:
        cn = cn.replace("保护`field`。", "保护`field`。\n\n增加一段。")
    assert technical_sections(en) != technical_sections(cn)


def test_bilingual_executable_examples_are_identical():
    en, cn = [p.read_text(encoding="utf-8") for p in READMES]
    assert executable_examples(en) == executable_examples(cn)


@pytest.mark.parametrize("mutation", ["model", "prompt", "policy", "missing_block"])
def test_example_alignment_detects_executable_drift(mutation):
    good = '```python\nquery(model="example", prompt="Hello")\n```\n\n```bash\nexport POLICY=never\n```\n'
    if mutation == "model":
        bad = good.replace('model="example"', 'model="different"')
    elif mutation == "prompt":
        bad = good.replace('prompt="Hello"', 'prompt="different"')
    elif mutation == "policy":
        bad = good.replace("POLICY=never", "POLICY=always")
    else:
        bad = good.split("\n\n", 1)[0]
    assert executable_examples(good) != executable_examples(bad)


def test_bilingual_agent_rows_bind_the_same_models_and_commands():
    en, cn = [p.read_text(encoding="utf-8") for p in READMES]
    a = table(en, "## Supported Agents")
    b = table(cn, "## 支持的 Agent")

    def bindings(rows):
        return [(r[0], r[1], r[2], re.findall(r"`([^`]+)`", r[3])) for r in rows]

    assert bindings(a) == bindings(b)


def test_bilingual_external_reference_targets_match():
    targets = []
    for path in READMES:
        document = path.read_text(encoding="utf-8")
        targets.append(Counter(re.findall(r"\]\((https?://[^)]+)\)", document)))
    assert targets[0] == targets[1]


@pytest.mark.parametrize("path", READMES, ids=lambda p: p.name)
def test_readme_project_table_identity_and_self_label(path):
    document = path.read_text(encoding="utf-8")
    rows = table(document, "## AIXP Labs [aixp.dev](https://aixp.dev)")
    project_identity(rows)
    labels = [row for row in rows if "**(this project)**" in row[1] or "**（本项目）**" in row[1]]
    assert len(labels) == 1 and labels[0][0].startswith("[SoulACP]")


@pytest.mark.parametrize("mutation", ["missing", "reordered", "duplicate", "wrong_url"])
def test_project_table_rejects_drift(mutation):
    rows = [[f"[{name}](https://{name.lower()}.dev)", "synthetic", f"{name.lower()}.dev"] for name in PROJECTS]
    project_identity(rows)
    if mutation == "missing":
        rows.pop()
    elif mutation == "reordered":
        rows[0], rows[1] = rows[1], rows[0]
    elif mutation == "duplicate":
        rows[-1] = rows[0].copy()
    else:
        rows[0][0] = "[HSAW](https://invalid.example)"
    with pytest.raises(AssertionError):
        project_identity(rows)


def test_bilingual_environment_defaults_agree():
    en, cn = [p.read_text(encoding="utf-8") for p in READMES]
    a = table(en, "### Environment Variables")
    b = table(cn, "### 环境变量")
    translate = {"当前目录": "Current directory", "未设置": "unset"}
    assert [(r[0], r[1]) for r in a] == [(r[0], translate.get(r[1], r[1])) for r in b]
    assert len({r[0] for r in a}) == len(a)


@pytest.mark.parametrize("path", READMES, ids=lambda p: p.name)
def test_readme_quality_commands_use_existing_strict_gates(path):
    quality_commands(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("mutation", ["warnings", "tools", "build"])
def test_quality_command_check_rejects_regressions(mutation):
    good = "\n".join(QUALITY_COMMANDS)
    quality_commands(good)
    if mutation == "warnings":
        bad = good.replace(" -W error", "")
    elif mutation == "tools":
        bad = good.replace(" examples/ tools/", " examples/")
    else:
        bad = good.replace("python tools/build_release.py", "python -m build")
    with pytest.raises(AssertionError):
        quality_commands(bad)


@pytest.mark.parametrize("path", READMES, ids=lambda p: p.name)
def test_python_examples_parse_and_import_only_public_symbols(path):
    document = path.read_text(encoding="utf-8")
    blocks = re.findall(r"(?ms)^```python\n(.*?)^```", document)
    assert blocks
    for block in blocks:
        parsed = ast.parse(block)
        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module == "soulacp":
                assert all(name.name in soulacp.__all__ for name in node.names)
    names = set(re.findall(r"`(ACP[A-Za-z]+Error|RPCError)`", document))
    assert names and names <= set(soulacp.__all__)


@pytest.mark.parametrize(
    "path", [*sorted(ROOT.glob("*.md")), ROOT / ".github/pull_request_template.md"], ids=lambda p: p.name
)
def test_local_document_targets_exist(path):
    document = path.read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)]+)\)", document):
        target = target.split("#", 1)[0]
        if target and not re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            assert (path.parent / target).is_file(), "missing local document target"


def test_contribution_guides_cover_real_sdk_and_doc_regressions():
    for name in ["CONTRIBUTING.md", "CONTRIBUTING_CN.md"]:
        document = (ROOT / name).read_text(encoding="utf-8")
        assert "OpenTelemetry SDK" in document
        assert "python -m pytest tests/test_documentation_consistency.py -W error -q" in document
        assert "--cov=soulacp --cov-branch --cov-report=term" in document
        assert "tools/run_hooks.py --python" in document


def test_maintainer_template_distinguishes_qualification_scopes():
    document = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    for phrase in [
        "Strict-warning",
        "dependency audits",
        "new output directory",
        "GitHub-hosted CI",
        "authenticated provider",
    ]:
        assert phrase in document


def heading_ids(document):
    """GitHub-style fragments for the headings used by these repository docs."""
    text = re.sub(r"(?ms)^```[^\n]*\n.*?^```", "", document)
    result = set(re.findall(r'<a\s+id="([^"]+)"', text))
    counts = Counter()
    for title in re.findall(r"(?m)^#{1,6} (.+)$", text):
        title = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", title)
        title = re.sub(r"<[^>]+>", "", title).lower()
        slug = "".join(c for c in title if c in {" ", "_", "-"} or unicodedata.category(c)[0] in {"L", "N"})
        slug = slug.replace(" ", "-")
        count = counts[slug]
        counts[slug] += 1
        result.add(slug + (f"-{count}" if count else ""))
    return result


def local_links(document, parent):
    for target in re.findall(r"\]\(([^)]+)\)", document):
        if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        file, separator, fragment = target.partition("#")
        path = parent / unquote(file) if file else None
        if path is not None:
            assert path.is_file(), "missing local document target"
        if separator and fragment:
            linked = path.read_text(encoding="utf-8") if path is not None else document
            assert unquote(fragment) in heading_ids(linked), "missing local document fragment"


@pytest.mark.parametrize(
    "path", [*sorted(ROOT.glob("*.md")), ROOT / ".github/pull_request_template.md"], ids=lambda p: p.name
)
def test_document_fragment_targets_exist(path):
    local_links(path.read_text(encoding="utf-8"), path.parent)


@pytest.mark.parametrize("mutation", ["missing_file", "missing_fragment", "renamed_heading"])
def test_document_fragment_checker_rejects_broken_links(tmp_path, mutation):
    good = "# Sample\n\n## Scope\n\n[Scope](#scope)\n\n[Other](other.md#policy)\n"
    (tmp_path / "other.md").write_text("# Other\n\n## Policy\n", encoding="utf-8")
    local_links(good, tmp_path)
    if mutation == "missing_file":
        bad = good.replace("other.md", "absent.md")
    elif mutation == "missing_fragment":
        bad = good.replace("#scope)", "#absent)")
    else:
        bad = good.replace("## Scope", "## Renamed")
    with pytest.raises(AssertionError):
        local_links(bad, tmp_path)


def test_document_fragments_support_explicit_ids_duplicates_and_unicode(tmp_path):
    document = '# 示例\n\n## API `name`\n\n## API `name`\n\n<a id="explicit"></a>\n\n[One](#api-name) [Two](#api-name-1) [CN](#示例) [ID](#explicit)\n'
    local_links(document, tmp_path)


@pytest.mark.parametrize("path", READMES, ids=lambda p: p.name)
def test_readme_explains_constructor_and_dotenv_boundaries(path):
    document = path.read_text(encoding="utf-8")
    assert "`ACPConfig()`" in document
    assert "`.env`" in document
    assert "`ACPConfig.from_env()`" in document


@pytest.mark.parametrize("path", [*READMES, ROOT / "SECURITY.md"], ids=lambda p: p.name)
def test_permission_selection_is_not_a_host_service_deny_switch(path):
    document = path.read_text(encoding="utf-8")
    for phrase in ("`auto_approve_permissions=False`", "`session/request_permission`", "`fs/*`", "`terminal/*`"):
        assert phrase in document


def test_config_documentation_does_not_reference_nonexistent_registry_or_unverified_policy():
    document = (ROOT / "src/soulacp/config.py").read_text(encoding="utf-8")
    assert "ModelRegistry" not in document
    assert "sandbox_permissions" not in document
    assert "ACPConfig.from_env()" in ACPConfig.__doc__
    assert ".env" in ACPConfig.__doc__


def test_documented_environment_loading_precedence_without_provider_calls(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("ACP_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ACP_POOL_SIZE=1\n", encoding="utf-8")
    assert ACPConfig().pool_size == ACPConfig.from_env().pool_size == 5
    monkeypatch.setenv("ACP_POOL_SIZE", "2")
    assert ACPConfig().pool_size == 5
    assert ACPConfig.from_env().pool_size == 2
    assert ACPConfig.from_env(pool_size=3).pool_size == 3


@pytest.mark.parametrize(
    "path,heading", [(READMES[0], "### Programmatic"), (READMES[1], "### 代码配置")], ids=["README.md", "README_CN.md"]
)
def test_documented_programmatic_config_constructs_without_launching_provider(path, heading):
    body = section(path.read_text(encoding="utf-8"), heading)
    blocks = re.findall(r"(?ms)^```python\n(.*?)^```", body)
    assert len(blocks) == 1
    # This configuration-only block must never grow a provider call or file write.
    parsed = ast.parse(blocks[0])
    calls = [node for node in ast.walk(parsed) if isinstance(node, ast.Call)]
    assert len(calls) == 1 and isinstance(calls[0].func, ast.Name) and calls[0].func.id == "ACPConfig"
    scope = {}
    exec(compile(parsed, "<synthetic-documented-config>", "exec"), scope)
    config = scope["config"]
    assert isinstance(config, ACPConfig)
    assert config.provider == "claude" and config.pool_max_connections == 8
    assert config.auto_approve_permissions is True
    assert config.additional_directories == ["/absolute/path/to/shared"]


def paragraph_technical_sections(document):
    without_fences = re.sub(r"(?ms)^```[^\n]*\n.*?^```", "", document)
    headings = list(re.finditer(r"(?m)^(#{1,6}) .+$", without_fences))
    result = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(without_fences)
        body = without_fences[heading.end() : end].strip()
        paragraphs = re.split(r"\n\s*\n", body) if body else []
        result.append([Counter(re.findall(r"(?<!`)`([^`\r\n]+)`(?!`)", paragraph)) for paragraph in paragraphs])
    return result


def test_bilingual_technical_paragraph_order_matches():
    en, cn = [path.read_text(encoding="utf-8") for path in READMES]
    assert paragraph_technical_sections(en) == paragraph_technical_sections(cn)


def test_paragraph_alignment_detects_swapped_scope_without_aggregate_drift():
    first = "# Sample\n\n## Policy\n\nKeep `first` private.\n\nKeep `second` bounded.\n"
    second = "# 示例\n\n## 策略\n\n限制`second`。\n\n保护`first`。\n"
    assert technical_sections(first) == technical_sections(second)
    assert paragraph_technical_sections(first) != paragraph_technical_sections(second)


@pytest.mark.parametrize(
    "path,heading,fragment",
    [
        (READMES[0], "## Authentication and Service Terms", "#authentication-and-service-terms"),
        (READMES[1], "## 认证与服务条款", "#认证与服务条款"),
    ],
    ids=["README.md", "README_CN.md"],
)
def test_authentication_ownership_has_a_dedicated_entry_before_multiuser_examples(path, heading, fragment):
    document = path.read_text(encoding="utf-8")
    body = section(document, heading)
    assert f"]({fragment})" in document
    assert document.index(heading) < document.index("### ManagedSession + ProviderSessionStore")
    for token in ("`user_id`", "`FileCache`", "`auto_approve_permissions=False`", "`authenticate()`"):
        assert token in body


@pytest.mark.parametrize("path", READMES, ids=lambda path: path.name)
def test_gemini_warning_precedes_the_live_example(path):
    body = section(path.read_text(encoding="utf-8"), "### Gemini")
    assert body.index("`clearCachedCredentialFile`") < body.index("```python")
    assert "`authMethods[0]`" in body
    assert "0.51.0" in body


@pytest.mark.parametrize("path", READMES, ids=lambda path: path.name)
def test_codex_example_uses_provider_default_and_explains_credential_storage(path):
    body = section(path.read_text(encoding="utf-8"), "### Codex (OpenAI)")
    assert 'ManagedSession(provider="codex", model="codex-acp/default")' in body
    assert "`CODEX_HOME`" in body and "`cli_auth_credentials_store`" in body
    assert "https://learn.chatgpt.com/docs/auth?surface=cli" in body
    assert "https://learn.chatgpt.com/docs/models" in body
    assert "2026-10-14" in body


@pytest.mark.parametrize("path", READMES, ids=lambda path: path.name)
def test_architecture_keeps_legacy_cursor_outside_native_acp_base(path):
    text = path.read_text(encoding="utf-8")
    graph = re.findall(r"(?ms)^```text\n(.*?)^```", text)[0]
    native = next(line for line in graph.splitlines() if "ACPClientBase" in line)
    legacy = next(line for line in graph.splitlines() if "CursorCLIClient" in line)
    protocol = next(line for line in graph.splitlines() if re.search(r"\bACPClient\b", line))
    services = next(line for line in graph.splitlines() if "Services" in line)
    assert native.index("ACPClientBase") == legacy.index("CursorCLIClient")
    assert native.index("ACPClientBase") > protocol.index("ACPClient")
    assert services.index("Services") > native.index("ACPClientBase")


def test_makefile_build_directory_tracks_current_source_version():
    assert f"DIST_DIR ?= dist/{soulacp.__version__}\n" in (ROOT / "Makefile").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["CONTRIBUTING.md", "CONTRIBUTING_CN.md"])
def test_contribution_guides_include_untracked_hooks_and_verifier_origin(name):
    document = (ROOT / name).read_text(encoding="utf-8")
    assert "pre_commit run --files tests/test_fs_integrity_errors.py tests/test_owned_fs_shutdown.py" in document
    assert "`VIRTUAL_ENV`" in document and "`PATH`" in document


def test_bilingual_contribution_commands_match():
    en, cn = [(ROOT / name).read_text(encoding="utf-8") for name in ("CONTRIBUTING.md", "CONTRIBUTING_CN.md")]
    assert executable_examples(en) == executable_examples(cn)


def test_security_and_governance_document_current_migration_boundaries():
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "`clearCachedCredentialFile`" in security
    assert "UTF-8" in security and "CRLF" in security
    governance = (ROOT / "GOVERNANCE.md").read_text(encoding="utf-8")
    assert "`10004`" in governance and "`-32000`" in governance
    assert "CRLF" in governance and "UTF-8" in governance


@pytest.mark.parametrize("name", ["basic_chat.py", "streaming.py", "session_management.py", "multi_agent.py"])
def test_example_documentation_warns_about_default_permissions(name):
    document = ast.get_docstring(ast.parse((ROOT / "examples" / name).read_text(encoding="utf-8")))
    assert document and "permission" in document.lower() and "default" in document.lower()
