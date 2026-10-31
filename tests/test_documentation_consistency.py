"""Offline documentation contracts, without provider calls or sibling checkouts."""

import ast
import re
from collections import Counter
from pathlib import Path

import pytest

import soulacp

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
    "python tools/build_release.py --outdir dist/0.2.1",
    "python tools/check_sdist.py dist/0.2.1/soulacp-0.2.1.tar.gz",
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
