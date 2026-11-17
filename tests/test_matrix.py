"""Every harness must convert to every other harness, losslessly."""

from pathlib import Path

import pytest

from skill_bridge.converter import convert_skill
from skill_bridge.models import AgentFormat
from skill_bridge.parser import load_skill, parse_frontmatter
from skill_bridge.provenance import is_bridge_generated, read_marker
from skill_bridge.validator import validate_skill

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "source-claude-code" / "code-review"
HARNESSES = [fmt for fmt in AgentFormat if fmt is not AgentFormat.GENERIC]


def read_frontmatter(skill_dir: Path) -> tuple[dict, str]:
    return parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))


@pytest.mark.parametrize("fmt", HARNESSES, ids=lambda f: f.value)
def test_export_to_each_harness(fmt, tmp_path):
    result = convert_skill(EXAMPLE, fmt, tmp_path / "out")

    assert (result.output_dir / "SKILL.md").is_file()
    fm, body = read_frontmatter(result.output_dir)
    assert fm["name"] == "code-review"
    assert "Review code" in fm["description"]
    assert "# Code Review" in body
    assert (result.output_dir / "references" / "STANDARDS.md").is_file()

    marker = read_marker(result.output_dir)
    assert marker["generated-by"] == "skill-bridge"
    assert marker["target-format"] == fmt.value
    assert marker["source-format"] == "claude-code"

    reloaded = load_skill(result.output_dir)
    assert validate_skill(reloaded, strict_dir_name=False).ok


@pytest.mark.parametrize("source_fmt", HARNESSES, ids=lambda f: f"from-{f.value}")
@pytest.mark.parametrize("target_fmt", HARNESSES, ids=lambda f: f"to-{f.value}")
def test_every_harness_pair(source_fmt, target_fmt, tmp_path):
    staged = convert_skill(EXAMPLE, source_fmt, tmp_path / "stage")
    result = convert_skill(staged.output_dir, target_fmt, tmp_path / "out")

    fm, body = read_frontmatter(result.output_dir)
    assert fm["name"] == "code-review"
    assert fm["description"]
    assert "# Code Review" in body
    assert "Review Checklist" in body
    assert (result.output_dir / "references" / "STANDARDS.md").is_file()
    assert is_bridge_generated(result.output_dir)
    assert validate_skill(load_skill(result.output_dir), strict_dir_name=False).ok


@pytest.mark.parametrize("fmt", HARNESSES, ids=lambda f: f.value)
def test_round_trip_is_idempotent(fmt, tmp_path):
    """harness -> openagents -> harness reproduces the first output exactly."""
    first = convert_skill(EXAMPLE, fmt, tmp_path / "1")
    via_standard = convert_skill(first.output_dir, AgentFormat.OPENAGENTS, tmp_path / "2")
    again = convert_skill(via_standard.output_dir, fmt, tmp_path / "3")

    assert (again.output_dir / "SKILL.md").read_text(encoding="utf-8") == (
        first.output_dir / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert (again.output_dir / "references" / "STANDARDS.md").is_file()


@pytest.mark.parametrize(
    "a,b",
    [(AgentFormat.CLAUDE_CODE, AgentFormat.HERMES), (AgentFormat.GEMINI, AgentFormat.COPILOT)],
)
def test_ping_pong_does_not_duplicate_sections(a, b, tmp_path):
    one = convert_skill(EXAMPLE, a, tmp_path / "a")
    two = convert_skill(one.output_dir, b, tmp_path / "b")
    three = convert_skill(two.output_dir, a, tmp_path / "c")
    four = convert_skill(three.output_dir, b, tmp_path / "d")

    text = (four.output_dir / "SKILL.md").read_text(encoding="utf-8")
    assert text.count("## When to Use") <= 1
    assert text.count("name: code-review") == 1
    assert (four.output_dir / "references" / "STANDARDS.md").is_file()
