from pathlib import Path

import pytest

from skill_bridge.converter import convert_skill
from skill_bridge.models import AgentFormat
from skill_bridge.parser import load_skill, parse_frontmatter
from skill_bridge.validator import validate_skill

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "source-claude-code" / "code-review"


def test_parse_frontmatter():
    text = EXAMPLE.joinpath("SKILL.md").read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    assert fm["name"] == "code-review"
    assert "Review code" in fm["description"]
    assert "# Code Review" in body


def test_load_skill():
    bundle = load_skill(EXAMPLE)
    assert bundle.name == "code-review"
    assert bundle.source_format == AgentFormat.CLAUDE_CODE
    assert "references" in bundle.supporting_dirs() or (EXAMPLE / "references").is_dir()


def test_opencode_global_skill_is_detected(tmp_path):
    skill = tmp_path / ".config" / "opencode" / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: OpenCode global skill.\n---\n\n# Demo\n",
        encoding="utf-8",
    )

    assert load_skill(skill).source_format == AgentFormat.OPENCODE


def test_validate_skill():
    bundle = load_skill(EXAMPLE)
    report = validate_skill(bundle, strict_dir_name=True)
    assert report.ok


@pytest.mark.parametrize(
    "fmt",
    [
        AgentFormat.HERMES,
        AgentFormat.CODEX,
        AgentFormat.CURSOR,
        AgentFormat.CLAUDE_CODE,
        AgentFormat.OPENAGENTS,
        AgentFormat.WINDSURF,
        AgentFormat.CLINE,
        AgentFormat.OPENCODE,
        AgentFormat.OPENCLAW,
    ],
)
def test_convert_to_formats(fmt, tmp_path):
    out = tmp_path / fmt.value
    result = convert_skill(EXAMPLE, fmt, out)
    skill_md = result.output_dir / "SKILL.md"
    assert skill_md.is_file()
    text = skill_md.read_text(encoding="utf-8")
    assert "name: code-review" in text
    assert "description:" in text
    assert "# Code Review" in text


def test_hermes_adds_metadata(tmp_path):
    out = tmp_path / "hermes"
    result = convert_skill(EXAMPLE, AgentFormat.HERMES, out)
    text = (result.output_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "metadata:" in text
    assert "hermes:" in text
    assert "tags:" in text
