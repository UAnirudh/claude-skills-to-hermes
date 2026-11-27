"""`doctor`: stale, out-of-sync and lossy copies in one read-only pass.

Each finding class is something a user can act on, so each gets a test: a
deleted source (fix with `sync --prune`), an edited source that was never
re-synced (fix with `sync`), and frontmatter the target cannot express (which
no command can fix, so `--strict` must not fail on it).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml
from click.testing import CliRunner

from skill_bridge import cache
from skill_bridge.cli import main
from skill_bridge.config import SkillLocation
from skill_bridge.doctor import EDITED, LOSSY, OUT_OF_SYNC, STALE, diagnose
from skill_bridge.models import AgentFormat
from skill_bridge.provenance import tree_hash, write_marker
from skill_bridge.sync import run_sync


def write_skill(root: Path, name: str, *, frontmatter: str = "") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name} used by the doctor tests.\n"
        f"{frontmatter}"
        "---\n\n"
        "# Demo\n\n"
        "Body paragraph.\n",
        encoding="utf-8",
    )
    return skill_dir


def write_config(tmp_path: Path, source: Path, target: Path) -> Path:
    config = {
        "default_profile": "test",
        "profiles": {
            "test": {
                "source": {"format": "generic", "path": str(source)},
                "targets": [{"format": "hermes", "path": str(target)}],
                "watch": True,
                "validate": True,
            }
        },
    }
    path = tmp_path / "skill-bridge.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def hermes(target: Path) -> list[SkillLocation]:
    return [SkillLocation(format=AgentFormat.HERMES, path=target)]


def forbid_conversion(monkeypatch) -> None:
    """Fail if doctor re-converts instead of trusting the marker fingerprints."""

    def boom(*args, **kwargs):
        raise AssertionError("doctor re-converted a copy that had fingerprints")

    monkeypatch.setattr(cache, "convert_skill", boom)


def test_a_fresh_sync_leaves_nothing_to_report(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)

    report = diagnose(hermes(target))

    assert report.checked == 1
    assert report.clean
    assert report.drift == []

    result = CliRunner().invoke(main, ["doctor", "--config", str(config), "--strict"])

    assert result.exit_code == 0, result.output
    assert "nothing to fix" in result.output


def test_doctor_reports_a_stale_copy_and_strict_fails(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    shutil.rmtree(source / "demo")

    report = diagnose(hermes(target))

    assert [finding.kind for finding in report.findings] == [STALE]
    assert report.findings[0].skill_name == "demo"
    assert report.drift == report.findings

    # Without --strict a finding is a report, not a failure.
    reported = CliRunner().invoke(main, ["doctor", "--config", str(config)])
    assert reported.exit_code == 0, reported.output
    assert "Source gone" in reported.output

    strict = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--strict"]
    )
    assert strict.exit_code == 1, strict.output


def test_doctor_reports_a_copy_that_no_longer_matches_its_source(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    skill = write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)

    # Edit the source without re-syncing: the copy is now behind.
    (skill / "SKILL.md").write_text(
        (skill / "SKILL.md").read_text(encoding="utf-8")
        + "\n## Added later\n\nThis section never reached the copy.\n",
        encoding="utf-8",
    )

    report = diagnose(hermes(target))

    assert [finding.kind for finding in report.findings] == [OUT_OF_SYNC]
    assert "source changed" in report.findings[0].detail

    strict = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--strict"]
    )
    assert strict.exit_code == 1, strict.output
    assert "Out of sync" in strict.output


def test_doctor_reports_lossy_fields_without_failing_strict(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    # Hermes has no `model` field, so the conversion is lossy for it.
    write_skill(source, "demo", frontmatter="model: opus\n")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)

    report = diagnose(hermes(target))

    assert [finding.kind for finding in report.findings] == [LOSSY]
    assert "model" in report.findings[0].detail
    assert report.drift == []

    # Lossy is inherent to the format pair, so it never fails the drift gate.
    strict = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--strict"]
    )

    assert strict.exit_code == 0, strict.output
    assert "Lossy fields" in strict.output


def test_doctor_ignores_hand_written_skills(tmp_path):
    target = tmp_path / "tgt"
    write_skill(target, "native")

    report = diagnose(hermes(target))

    assert report.checked == 0
    assert report.findings == []


def test_markers_record_fingerprints_for_source_and_copy(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)

    marker = json.loads(
        (target / "demo" / ".skill-bridge.json").read_text(encoding="utf-8")
    )

    assert marker["source-sha"] == tree_hash(source / "demo")
    assert marker["output-sha"] == tree_hash(target / "demo")


def test_doctor_trusts_the_fingerprints_instead_of_reconverting(tmp_path, monkeypatch):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    forbid_conversion(monkeypatch)

    report = diagnose(hermes(target))

    assert report.checked == 1
    assert report.clean


def test_doctor_names_the_file_it_could_not_name_before(tmp_path):
    """A copy that added a file reports that file, not just its SKILL.md."""
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    (target / "demo" / "NOTES.md").write_text("kept by hand\n", encoding="utf-8")

    report = diagnose(hermes(target))

    assert [finding.kind for finding in report.findings] == [EDITED]
    differences = report.findings[0].files
    assert [(item.name, item.status, item.line) for item in differences] == [
        ("NOTES.md", "extra", 1)
    ]


def test_doctor_spots_a_hand_edited_copy_and_names_the_line(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    copy = target / "demo" / "SKILL.md"
    copy.write_text(
        copy.read_text(encoding="utf-8") + "\n<!-- edited by hand -->\n",
        encoding="utf-8",
    )
    report = diagnose(hermes(target))

    assert [finding.kind for finding in report.findings] == [EDITED]
    assert report.findings[0].detail == "edited by hand since it was written"
    differences = report.findings[0].files
    assert [item.name for item in differences] == ["SKILL.md"]
    assert differences[0].status == "changed"
    # The appended comment is past the converted file, so not line 1.
    assert differences[0].line > 1


def test_doctor_spots_an_edited_source_and_names_the_line(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    skill = source / "demo" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "\n## Added later\n", encoding="utf-8"
    )
    report = diagnose(hermes(target))

    assert [finding.kind for finding in report.findings] == [OUT_OF_SYNC]
    assert report.findings[0].detail == "source changed since this copy was converted"
    differences = report.findings[0].files
    assert [item.name for item in differences] == ["SKILL.md"]
    assert differences[0].status == "changed"
    assert differences[0].line > 1


def test_doctor_reconverts_a_marker_without_fingerprints(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    # A copy written by an older skill-bridge: no fingerprints to compare.
    write_marker(
        target / "demo",
        source_format=AgentFormat.GENERIC,
        target_format=AgentFormat.HERMES,
        source_path=source / "demo",
    )
    skill = source / "demo" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "\n## Added later\n", encoding="utf-8"
    )

    report = diagnose(hermes(target))

    assert [finding.kind for finding in report.findings] == [EDITED]
    assert "SKILL.md" in report.findings[0].detail
    assert "no fingerprints" in report.findings[0].detail


def test_fix_refreshes_the_copy_behind_its_source_and_spares_the_edited_one(
    tmp_path,
):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "behind")
    write_skill(source, "edited")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)

    # `behind` falls behind its source; `edited` is only changed in the copy.
    behind_source = source / "behind" / "SKILL.md"
    behind_source.write_text(
        behind_source.read_text(encoding="utf-8") + "\n## Added later\n",
        encoding="utf-8",
    )
    edited_copy = target / "edited" / "SKILL.md"
    edited_copy.write_text(
        edited_copy.read_text(encoding="utf-8") + "\n<!-- hand written -->\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["doctor", "--config", str(config), "--fix"])

    assert result.exit_code == 0, result.output
    # The stale-behind copy is regenerated...
    assert "## Added later" in (target / "behind" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    # ...and the hand-edited copy survives untouched.
    assert "<!-- hand written -->" in edited_copy.read_text(encoding="utf-8")
    assert "Edited by hand" in result.output
    assert "Left 1 hand-edited" in result.output

    # It still needs a decision, so the gate keeps failing until one is made.
    strict = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--strict"]
    )
    assert strict.exit_code == 1, strict.output


def test_fix_never_overwrites_a_copy_that_is_both_edited_and_behind(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    skill = source / "demo" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "\n## Added later\n", encoding="utf-8"
    )
    copy = target / "demo" / "SKILL.md"
    copy.write_text(
        copy.read_text(encoding="utf-8") + "\n<!-- hand written -->\n",
        encoding="utf-8",
    )

    report = diagnose(hermes(target))
    result = CliRunner().invoke(main, ["doctor", "--config", str(config), "--fix"])

    assert [finding.kind for finding in report.findings] == [EDITED]
    assert "source changed too" in report.findings[0].detail
    assert "<!-- hand written -->" in copy.read_text(encoding="utf-8")
    assert "## Added later" not in copy.read_text(encoding="utf-8")


def test_fix_prunes_stale_copies_and_refreshes_out_of_sync_ones(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "keep")
    gone = write_skill(source, "gone")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    assert (target / "gone" / "SKILL.md").is_file()

    shutil.rmtree(gone)
    keep = source / "keep" / "SKILL.md"
    keep.write_text(
        keep.read_text(encoding="utf-8") + "\n## Added later\n", encoding="utf-8"
    )

    result = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--fix", "--strict"]
    )

    assert result.exit_code == 0, result.output
    # The stale copy is gone, the live one caught up with its source.
    assert not (target / "gone").exists()
    assert "## Added later" in (target / "keep" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "Fixed" in result.output
    assert "No drift left" in result.output


def test_fix_leaves_lossy_findings_alone(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    # Hermes has no `model` field: no conversion can put it back.
    write_skill(source, "demo", frontmatter="model: opus\n")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    copy = target / "demo" / "SKILL.md"
    before = copy.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--fix", "--strict"]
    )

    assert result.exit_code == 0, result.output
    assert copy.read_text(encoding="utf-8") == before
    assert "Lossy fields" in result.output


def test_json_output_carries_the_findings_and_the_strict_exit_code(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "behind")
    write_skill(source, "edited", frontmatter="model: opus\n")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    behind_source = source / "behind" / "SKILL.md"
    behind_source.write_text(
        behind_source.read_text(encoding="utf-8") + "\n## Added later\n",
        encoding="utf-8",
    )
    edited_copy = target / "edited" / "SKILL.md"
    edited_copy.write_text(
        edited_copy.read_text(encoding="utf-8") + "\n<!-- hand written -->\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--json", "--strict"]
    )

    assert result.exit_code == 1, result.output
    # stdout is the document and nothing else, so CI can parse it directly.
    payload = json.loads(result.output)
    assert payload["profile"] == "test"
    assert payload["checked"] == 2
    assert payload["clean"] is False
    assert payload["drift"] == 2
    assert payload["counts"] == {
        "unfetched": 0,
        "stale": 0,
        "out-of-sync": 1,
        "edited": 1,
        "lossy": 1,
        "outdated": 0,
    }
    assert {finding["kind"] for finding in payload["findings"]} == {
        "out-of-sync",
        "edited",
        "lossy",
    }
    behind = next(
        finding for finding in payload["findings"] if finding["skill"] == "behind"
    )
    assert set(behind) == {
        "kind",
        "skill",
        "target",
        "path",
        "file",
        "detail",
        "files",
    }
    assert behind["target"] == "hermes"
    assert behind["path"] == str(target / "behind")
    assert behind["file"] == str(target / "behind" / "SKILL.md")
    assert behind["detail"] == "source changed since this copy was converted"


def test_json_output_on_a_clean_profile_is_a_single_document(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)

    result = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--json", "--strict"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["clean"] is True
    assert payload["drift"] == 0
    assert payload["checked"] == 1
    assert payload["findings"] == []
    assert "fixed" not in payload


def test_json_with_fix_reports_what_the_repair_did(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "gone")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    shutil.rmtree(source / "gone")

    result = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--json", "--fix", "--strict"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["fixed"] == {
        "stale": 1,
        "out_of_sync": 0,
        "conversions": 0,
        "edited_left": 0,
        "lossy_left": 0,
    }
    assert payload["clean"] is True
    assert not (target / "gone").exists()


def test_fix_rewrites_nothing_when_the_profile_is_healthy(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    copy = target / "demo" / "SKILL.md"
    before = copy.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--fix", "--strict"]
    )

    assert result.exit_code == 0, result.output
    assert copy.read_text(encoding="utf-8") == before
    assert "nothing to fix" in result.output
    assert "Fixed" not in result.output
