"""Every harness gets its own native fields, and nothing is lost on the way.

These tests are the contract behind "install it on your harness and skills
meant for other harnesses work there": whatever harness a skill came from, the
output carries only fields the target reads, intent the target cannot express
is parked in metadata, and converting back restores it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_bridge.compat import (
    NATIVE_FIELDS,
    as_tool_list,
    manual_invocation,
    native_fields,
)
from skill_bridge.converter import convert_skill
from skill_bridge.formats.base import get_exporter, load_exporters
from skill_bridge.models import AgentFormat
from skill_bridge.parser import detect_source_format, load_skill, parse_frontmatter

HARNESSES = [fmt for fmt in AgentFormat if fmt is not AgentFormat.GENERIC]
SPEC_CORE = {"name", "description"}

RICH_SKILL = """---
name: rich-skill
description: Demo skill with harness-specific fields for conversion tests.
license: MIT
allowed-tools: Read Grep Bash
disable-model-invocation: true
model: sonnet
argument-hint: "[path]"
paths:
  - "src/**"
user-invocable: true
metadata:
  owner: platform-team
---

# Rich Skill

Do the thing.
"""


def write_rich_skill(root: Path) -> Path:
    skill = root / "rich-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(RICH_SKILL, encoding="utf-8")
    return skill


def frontmatter_of(skill_dir: Path) -> tuple[dict, str]:
    return parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))


@pytest.mark.parametrize("fmt", HARNESSES, ids=lambda f: f.value)
def test_each_harness_gets_only_fields_it_reads(fmt, tmp_path):
    source = write_rich_skill(tmp_path / "src")
    result = convert_skill(source, fmt, tmp_path / "out")

    fm, _ = frontmatter_of(result.output_dir)
    emitted = set(fm) - SPEC_CORE
    assert emitted <= set(native_fields(fmt)), (
        f"{fmt.value} output carries fields it does not read: "
        f"{sorted(emitted - set(native_fields(fmt)))}"
    )


@pytest.mark.parametrize("fmt", HARNESSES, ids=lambda f: f.value)
def test_unexpressible_fields_survive_a_round_trip(fmt, tmp_path):
    """Claude extras must come back after a hop through a minimal harness."""
    source = write_rich_skill(tmp_path / "src")
    there = convert_skill(source, fmt, tmp_path / "there")
    back = convert_skill(there.output_dir, AgentFormat.CLAUDE_CODE, tmp_path / "back")

    fm, _ = frontmatter_of(back.output_dir)
    assert fm["allowed-tools"] == "Read Grep Bash"
    assert fm["model"] == "sonnet"
    assert fm["argument-hint"] == "[path]"
    assert fm["paths"] == ["src/**"]
    assert "skill-bridge" not in (fm.get("metadata") or {}), (
        "restored fields must be removed from the carrier metadata"
    )


def test_manual_invocation_becomes_each_harnesss_equivalent(tmp_path):
    source = write_rich_skill(tmp_path / "src")

    cursor = convert_skill(source, AgentFormat.CURSOR, tmp_path / "cursor")
    cursor_fm, _ = frontmatter_of(cursor.output_dir)
    assert cursor_fm["disable-model-invocation"] is True

    opencode = convert_skill(source, AgentFormat.OPENCODE, tmp_path / "opencode")
    opencode_fm, _ = frontmatter_of(opencode.output_dir)
    assert opencode_fm["disable-model-invocation"] is True

    hermes = convert_skill(source, AgentFormat.HERMES, tmp_path / "hermes")
    hermes_fm, _ = frontmatter_of(hermes.output_dir)
    assert "disable-model-invocation" not in hermes_fm
    assert hermes_fm["metadata"]["hermes"]["category"] == "workflow"

    codex = convert_skill(source, AgentFormat.CODEX, tmp_path / "codex")
    codex_fm, _ = frontmatter_of(codex.output_dir)
    assert "disable-model-invocation" not in codex_fm
    assert codex_fm["metadata"]["skill-bridge"]["manual-invocation"] is True


def test_model_only_triggers_become_user_invocable_false_in_claude(tmp_path):
    """Devin's ``triggers: [model]`` is model-only, so Claude must not say true."""
    source = tmp_path / "src" / "model-only"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\n"
        "name: model-only\n"
        "description: A skill the model runs on its own, never the user.\n"
        "triggers:\n"
        "  - model\n"
        "---\n\n# Model Only\n\nLet the model decide when to use this.\n",
        encoding="utf-8",
    )

    claude = convert_skill(source, AgentFormat.CLAUDE_CODE, tmp_path / "claude")
    claude_fm, _ = frontmatter_of(claude.output_dir)
    assert claude_fm["user-invocable"] is False
    assert "disable-model-invocation" not in claude_fm

    copilot = convert_skill(source, AgentFormat.COPILOT, tmp_path / "copilot")
    copilot_fm, _ = frontmatter_of(copilot.output_dir)
    assert copilot_fm["user-invocable"] is False
    assert "disable-model-invocation" not in copilot_fm


def test_user_only_triggers_are_still_manual_only_in_claude(tmp_path):
    source = tmp_path / "src" / "user-only"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\n"
        "name: user-only\n"
        "description: A skill only the user may invoke.\n"
        "triggers:\n"
        "  - user\n"
        "---\n\n# User Only\n",
        encoding="utf-8",
    )

    claude = convert_skill(source, AgentFormat.CLAUDE_CODE, tmp_path / "claude")
    claude_fm, _ = frontmatter_of(claude.output_dir)
    assert claude_fm["disable-model-invocation"] is True
    assert claude_fm.get("user-invocable") is not True


def test_allowed_tools_are_shaped_per_harness(tmp_path):
    source = write_rich_skill(tmp_path / "src")

    devin = convert_skill(source, AgentFormat.DEVIN, tmp_path / "devin")
    devin_fm, _ = frontmatter_of(devin.output_dir)
    assert devin_fm["allowed-tools"] == ["Read", "Grep", "Bash"]

    claude = convert_skill(source, AgentFormat.CLAUDE_CODE, tmp_path / "claude")
    claude_fm, _ = frontmatter_of(claude.output_dir)
    assert claude_fm["allowed-tools"] == "Read Grep Bash"


def test_a_list_allowed_tools_stringifies_for_claude(tmp_path):
    source = tmp_path / "src" / "listy"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\n"
        "name: listy\n"
        "description: Skill whose tools are written as a YAML list.\n"
        "allowed-tools:\n"
        "  - Read\n"
        "  - Grep\n"
        "---\n\n# Listy\n",
        encoding="utf-8",
    )
    result = convert_skill(source, AgentFormat.CLAUDE_CODE, tmp_path / "out")
    fm, _ = frontmatter_of(result.output_dir)
    assert fm["allowed-tools"] == "Read Grep"


@pytest.mark.parametrize(
    "frontmatter,expected",
    [
        ({"disable-model-invocation": True}, True),
        ({"disable-model-invocation": "false"}, False),
        ({"user-invocable": False}, False),
        ({"triggers": ["user", "model"]}, False),
        ({"triggers": ["user"]}, True),
        ({"metadata": {"opencode": {"autoinvoke": False}}}, True),
        ({}, None),
    ],
)
def test_manual_invocation_is_read_from_any_harnesss_spelling(frontmatter, expected):
    assert manual_invocation(frontmatter) is expected


def test_as_tool_list_accepts_strings_and_lists():
    assert as_tool_list("Read, Grep  Bash") == ["Read", "Grep", "Bash"]
    assert as_tool_list(["Read", " "]) == ["Read"]


def test_every_exporter_has_a_field_table():
    load_exporters()
    for fmt in AgentFormat:
        assert fmt in NATIVE_FIELDS, f"{fmt.value} missing from the compatibility table"
        assert get_exporter(fmt) is not None


@pytest.mark.parametrize("fmt", HARNESSES, ids=lambda f: f.value)
def test_source_harness_is_detected_from_its_own_folder(fmt, tmp_path):
    """Any harness's directory layout is recognised on import."""
    load_exporters()
    subdir = get_exporter(fmt).default_output_subdir
    skill = tmp_path / "project" / subdir / "demo"
    skill.mkdir(parents=True)
    # Neutral frontmatter: this test is about the directory layout, and the
    # shared ``skills/`` folder has no harness-specific frontmatter to go on.
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: A neutral skill for layout detection.\n---\n\n# Demo\n",
        encoding="utf-8",
    )

    detected = load_skill(skill).source_format
    # Codex and Amp share .agents/skills, Generic and the standard share skills/.
    assert get_exporter(detected).default_output_subdir == subdir


@pytest.mark.parametrize(
    "subdir,expected",
    [
        (".config/opencode/skills", AgentFormat.OPENCODE),
        (".config/devin/skills", AgentFormat.DEVIN),
        (".codeium/windsurf/skills", AgentFormat.WINDSURF),
        (".clinerules/skills", AgentFormat.CLINE),
        (".config/goose/skills", AgentFormat.GOOSE),
        (".copilot/skills", AgentFormat.COPILOT),
    ],
)
def test_global_install_layouts_are_detected(subdir, expected, tmp_path):
    skill = tmp_path / "home" / subdir / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(RICH_SKILL, encoding="utf-8")
    assert detect_source_format(skill, {}) is expected
