"""The lint matrix: every exporter is checked against the format it writes.

`test_matrix.py` asks that every conversion keeps the skill intact. This asks a
narrower question of each output: is it what that harness reads? A field only
another harness understands, `allowed-tools` in the wrong shape, a `reference/`
folder, or Hermes output without its section are all invisible to the old
matrix and all wrong for the harness that receives the skill.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from skill_bridge.cli import main
from skill_bridge.converter import convert_skill
from skill_bridge.lint import SPECS, lint_matrix, lint_skill
from skill_bridge.models import AgentFormat
from skill_bridge.provenance import marker_path, write_marker

EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "source-claude-code"
    / "code-review"
)
FORMATS = list(AgentFormat)
BODY = "# Demo\n\n## When to Use\n\nAlways.\n"


@pytest.fixture(scope="module")
def matrix():
    return lint_matrix(EXAMPLE)


def rules(report) -> list[tuple[str, str]]:
    return [(issue.level, issue.rule) for issue in report.issues]


def write_skill(
    root: Path, frontmatter: str, *, body: str = BODY, name: str = "demo"
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8"
    )
    return skill_dir


def basic(fields: str = "") -> str:
    return f"name: demo\ndescription: Demo skill for the lint tests.\n{fields}"


def test_every_format_has_a_spec():
    assert set(SPECS) == set(AgentFormat)
    assert len(SPECS) == 17


@pytest.mark.parametrize("fmt", FORMATS, ids=lambda f: f.value)
def test_the_example_lints_clean_for_every_exporter(fmt, matrix):
    report = matrix[fmt]

    assert report.target_format is fmt
    # Exporter output is held to the whole spec, warnings included: anything
    # the lint has to say about a fresh conversion is a bug in the exporter.
    assert report.issues == []


def test_a_minimal_skill_lints_clean_for_every_format(tmp_path):
    skill_dir = write_skill(tmp_path, basic())

    for fmt in FORMATS:
        report = lint_skill(skill_dir, fmt)
        assert report.ok, (fmt, report.issues)


def test_a_field_the_harness_does_not_read_is_an_error(tmp_path):
    # `model` is Claude Code's; Codex documents name/description only, so the
    # key must be parked rather than left where Codex would ignore it.
    skill_dir = write_skill(tmp_path, basic("model: opus\n"))

    report = lint_skill(skill_dir, AgentFormat.CODEX)

    assert ("error", "fields") in rules(report)
    assert "model" in report.errors()[0].message


def test_allowed_tools_keeps_the_shape_the_harness_documents(tmp_path):
    as_list = write_skill(tmp_path / "list", basic("allowed-tools:\n  - Read\n"))
    as_string = write_skill(tmp_path / "string", basic("allowed-tools: Read Write\n"))

    assert lint_skill(as_list, AgentFormat.DEVIN).ok
    assert ("error", "allowed-tools") in rules(
        lint_skill(as_string, AgentFormat.DEVIN)
    )
    assert lint_skill(as_string, AgentFormat.CLAUDE_CODE).ok
    assert ("error", "allowed-tools") in rules(
        lint_skill(as_list, AgentFormat.CLAUDE_CODE)
    )


def test_invocation_fields_must_be_booleans(tmp_path):
    skill_dir = write_skill(tmp_path, basic("disable-model-invocation: sometimes\n"))

    report = lint_skill(skill_dir, AgentFormat.CURSOR)

    assert ("error", "disable-model-invocation") in rules(report)


def test_the_folder_name_is_reported_but_does_not_fail_the_lint(tmp_path):
    skill_dir = write_skill(tmp_path, basic(), name="other")

    report = lint_skill(skill_dir, AgentFormat.GEMINI)

    assert ("warning", "name") in rules(report)
    assert report.ok


def test_a_missing_description_is_an_error(tmp_path):
    skill_dir = write_skill(tmp_path, "name: demo\n")

    assert ("error", "description") in rules(lint_skill(skill_dir, AgentFormat.CODEX))


def test_a_reference_folder_must_be_references(tmp_path):
    skill_dir = write_skill(tmp_path, basic())
    (skill_dir / "reference").mkdir()

    assert ("error", "layout") in rules(lint_skill(skill_dir, AgentFormat.CLINE))

    (skill_dir / "references").mkdir()
    assert lint_skill(skill_dir, AgentFormat.CLINE).ok


def test_hermes_output_needs_its_section_and_block(tmp_path):
    no_section = write_skill(tmp_path / "section", basic(), body="# Demo\n")
    assert ("error", "sections") in rules(lint_skill(no_section, AgentFormat.HERMES))

    # The block is what the exporter promises, so a hand-written skill missing
    # it is only told, while a copy skill-bridge wrote without one is broken.
    hand_written = write_skill(tmp_path / "hand", basic())
    assert ("warning", "metadata") in rules(
        lint_skill(hand_written, AgentFormat.HERMES)
    )

    converted = write_skill(tmp_path / "converted", basic())
    write_marker(
        converted,
        source_format=AgentFormat.CLAUDE_CODE,
        target_format=AgentFormat.HERMES,
    )
    assert ("error", "metadata") in rules(lint_skill(converted, AgentFormat.HERMES))


def test_a_parked_field_the_harness_reads_is_an_error(tmp_path):
    parked = (
        basic()
        + "metadata:\n"
        "  skill-bridge:\n"
        "    extra:\n"
        "      version: 2.1\n"
    )
    skill_dir = write_skill(tmp_path, parked)

    report = lint_skill(skill_dir, AgentFormat.HERMES)

    # Hermes reads `version` at the top level, so parking it means the harness
    # never sees it.
    assert ("error", "carrier") in rules(report)


def test_a_stale_marker_says_which_format_the_copy_is_for(tmp_path):
    skill_dir = write_skill(tmp_path, basic())
    write_marker(
        skill_dir,
        source_format=AgentFormat.CLAUDE_CODE,
        target_format=AgentFormat.CURSOR,
    )

    report = lint_skill(skill_dir, AgentFormat.HERMES)

    assert ("warning", "marker") in rules(report)


def test_the_body_must_not_open_with_another_block(tmp_path):
    skill_dir = write_skill(tmp_path, basic(), body="---\nname: x\n---\n")

    assert ("error", "body") in rules(lint_skill(skill_dir, AgentFormat.CODEX))


def test_a_parked_field_is_lifted_back_out_by_the_next_hop(tmp_path):
    """A hop through a minimal harness must not duplicate what Hermes reads."""
    source = write_skill(tmp_path / "src", basic("version: 2.1\n"))
    codex = convert_skill(source, AgentFormat.CODEX, tmp_path / "codex")
    hermes = convert_skill(codex.output_dir, AgentFormat.HERMES, tmp_path / "hermes")

    report = lint_skill(hermes.output_dir, AgentFormat.HERMES)

    assert report.ok, report.issues
    text = (hermes.output_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "version: 2.1" in text
    assert text.count("version: 2.1") == 1


def test_lint_reads_the_format_from_the_marker(tmp_path):
    result = convert_skill(EXAMPLE, AgentFormat.HERMES, tmp_path / "out")

    run = CliRunner().invoke(main, ["lint", str(result.output_dir)])

    assert run.exit_code == 0, run.output
    assert "is what hermes expects" in run.output


def test_lint_fails_for_a_copy_checked_against_the_wrong_harness(tmp_path):
    result = convert_skill(EXAMPLE, AgentFormat.CODEX, tmp_path / "out")
    assert marker_path(result.output_dir).is_file()

    run = CliRunner().invoke(
        main, ["lint", str(result.output_dir), "--harness", "claude-code"]
    )

    assert run.exit_code == 1, run.output
    # Claude Code reads `origin`, so Codex parked it where Claude cannot see it.
    assert "origin" in run.output


def test_lint_without_a_marker_needs_a_harness(tmp_path):
    skill_dir = write_skill(tmp_path, basic())

    run = CliRunner().invoke(main, ["lint", str(skill_dir)])

    assert run.exit_code == 1
    assert "--harness" in run.output


def test_the_matrix_option_checks_every_format(tmp_path):
    run = CliRunner().invoke(main, ["lint", "--matrix", str(EXAMPLE)])

    assert run.exit_code == 0, run.output
    assert run.output.count("0 error(s), 0 warning(s)") == 17
    assert "17 format(s) checked, 0 failed." in run.output
