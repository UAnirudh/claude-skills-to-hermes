"""Invocation intent is invariant: a model-only skill can never become user-invocable.

The bug these tests lock down: Devin's ``triggers: [model]`` means the model
runs the skill on its own, with no user entry point. Converting that to Claude
Code must emit ``user-invocable: false`` — never default it to true — and the
intent must survive a hop through a harness that has no invocation field at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from skill_bridge.cli import main
from skill_bridge.compat import lossy_fields, manual_invocation
from skill_bridge.converter import convert_skill
from skill_bridge.models import AgentFormat
from skill_bridge.parser import parse_frontmatter

HARNESSES = [fmt for fmt in AgentFormat if fmt is not AgentFormat.GENERIC]

MODEL_ONLY = """---
name: model-only
description: A skill the model runs on its own, never the user.
triggers:
  - model
---

# Model Only
"""

MANUAL_ONLY = """---
name: manual-only
description: A skill only the user may invoke, never the model.
triggers:
  - user
---

# Manual Only
"""

NEUTRAL = """---
name: neutral
description: A skill that says nothing about who may invoke it.
---

# Neutral
"""


def write_skill(root: Path, name: str, text: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(text, encoding="utf-8")
    return skill


def frontmatter_of(skill_dir: Path) -> dict:
    return parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))[0]


@pytest.mark.parametrize("target", HARNESSES, ids=lambda f: f.value)
def test_model_only_never_comes_out_user_invocable(target, tmp_path):
    source = write_skill(tmp_path / "src", "model-only", MODEL_ONLY)

    result = convert_skill(source, target, tmp_path / "out")
    fm = frontmatter_of(result.output_dir)

    assert fm.get("user-invocable") is not True
    assert fm.get("disable-model-invocation") is not True


@pytest.mark.parametrize("target", HARNESSES, ids=lambda f: f.value)
def test_manual_only_never_comes_out_model_invocable(target, tmp_path):
    source = write_skill(tmp_path / "src", "manual-only", MANUAL_ONLY)

    result = convert_skill(source, target, tmp_path / "out")
    fm = frontmatter_of(result.output_dir)

    assert fm.get("disable-model-invocation") is not False
    assert fm.get("user-invocable") is not True
    assert manual_invocation(fm) in (True, None)


@pytest.mark.parametrize("target", HARNESSES, ids=lambda f: f.value)
def test_neutral_skill_invents_no_invocation(target, tmp_path):
    source = write_skill(tmp_path / "src", "neutral", NEUTRAL)

    result = convert_skill(source, target, tmp_path / "out")
    fm = frontmatter_of(result.output_dir)

    assert "user-invocable" not in fm
    assert "disable-model-invocation" not in fm
    carrier = (fm.get("metadata") or {}).get("skill-bridge") or {}
    assert "manual-invocation" not in carrier


@pytest.mark.parametrize("middle", HARNESSES, ids=lambda f: f"via-{f.value}")
def test_model_only_stays_model_only_through_any_hop(middle, tmp_path):
    source = write_skill(tmp_path / "src", "model-only", MODEL_ONLY)
    there = convert_skill(source, middle, tmp_path / "there")
    back = convert_skill(there.output_dir, AgentFormat.CLAUDE_CODE, tmp_path / "back")

    fm = frontmatter_of(back.output_dir)
    assert fm["user-invocable"] is False
    assert "disable-model-invocation" not in fm


@pytest.mark.parametrize("middle", HARNESSES, ids=lambda f: f"via-{f.value}")
def test_manual_only_stays_manual_only_through_any_hop(middle, tmp_path):
    source = write_skill(tmp_path / "src", "manual-only", MANUAL_ONLY)
    there = convert_skill(source, middle, tmp_path / "there")
    back = convert_skill(there.output_dir, AgentFormat.CLAUDE_CODE, tmp_path / "back")

    fm = frontmatter_of(back.output_dir)
    assert fm["disable-model-invocation"] is True
    assert fm.get("user-invocable") is not True


def test_lossy_fields_do_not_flag_translated_invocation():
    assert lossy_fields({"triggers": ["model"]}, AgentFormat.CLAUDE_CODE) == []
    assert "invocation" in lossy_fields({"triggers": ["model"]}, AgentFormat.CODEX)
    assert lossy_fields({"allowed-tools": "Read"}, AgentFormat.DEVIN) == []
    assert lossy_fields({"allowed-tools": "Read"}, AgentFormat.CODEX) == ["allowed-tools"]


def test_validate_fails_on_a_lossy_target(tmp_path):
    source = write_skill(
        tmp_path,
        "rich",
        "---\n"
        "name: rich\n"
        "description: A skill with fields Codex has no place for.\n"
        "allowed-tools: Read Grep\n"
        "model: sonnet\n"
        "---\n\n# Rich\n",
    )
    runner = CliRunner()

    warned = runner.invoke(main, ["validate", str(source), "--harness", "codex"])
    assert warned.exit_code == 0
    assert "LOSSY allowed-tools" in warned.output

    failed = runner.invoke(
        main, ["validate", str(source), "--harness", "codex", "--fail-on-lossy"]
    )
    assert failed.exit_code == 1

    fine = runner.invoke(
        main, ["validate", str(source), "--harness", "claude-code", "--fail-on-lossy"]
    )
    assert fine.exit_code == 0
    assert "Nothing lost" in fine.output


def test_validate_model_only_to_claude_has_nothing_lossy(tmp_path):
    source = write_skill(tmp_path, "model-only", MODEL_ONLY)

    result = CliRunner().invoke(
        main, ["validate", str(source), "--harness", "claude-code", "--fail-on-lossy"]
    )

    assert result.exit_code == 0, result.output
    assert "Nothing lost" in result.output


def test_fail_on_lossy_needs_a_harness(tmp_path):
    source = write_skill(tmp_path, "neutral", NEUTRAL)

    result = CliRunner().invoke(main, ["validate", str(source), "--fail-on-lossy"])

    assert result.exit_code == 2
    assert "needs --harness" in result.output
