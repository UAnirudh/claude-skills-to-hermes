"""A copy from an older skill-bridge is `outdated`, which is not drift.

The marker records the version that wrote the copy, so doctor can tell that a
converter fix since then has never reached it. The contents can still be exactly
what the source says, which is why nothing here repairs it: `sync` re-converts,
`--fix` leaves it, and `--strict` stays green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from skill_bridge import doctor as doctor_module
from skill_bridge import provenance
from skill_bridge.cli import main
from skill_bridge.config import load_config
from skill_bridge.doctor import (
    EDITED,
    LOSSY,
    OUTDATED,
    OUT_OF_SYNC,
    _version_key,
    diagnose,
)
from skill_bridge.sync import run_sync
from tests.test_annotations import SKILL, write_project

#: The version the tests pretend to be running, so the comparison is fixed.
RUNNING = "2.0.0"


def synced_project(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """A synced one-skill project, and the copy sync wrote into it."""
    config = write_project(tmp_path)
    monkeypatch.setattr(doctor_module, "package_version", lambda: RUNNING)
    run_sync(config_path=config)
    return config, tmp_path / "tgt" / "demo"


def record_version(copy: Path, version: str | None) -> None:
    """Pretend ``copy`` was written by another skill-bridge."""
    path = copy / ".skill-bridge.json"
    marker = json.loads(path.read_text(encoding="utf-8"))
    if version is None:
        marker.pop("version", None)
    else:
        marker["version"] = version
    path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")


def findings(config: Path) -> list:
    return diagnose(load_config(config).get_profile("test").targets).findings


def kinds(config: Path) -> list[str]:
    return [finding.kind for finding in findings(config)]


def test_a_copy_from_an_older_version_is_outdated(tmp_path, monkeypatch):
    config, copy = synced_project(tmp_path, monkeypatch)
    record_version(copy, "1.9.0")

    report = diagnose(load_config(config).get_profile("test").targets)

    assert [finding.kind for finding in report.findings] == [OUTDATED]
    detail = report.findings[0].detail
    assert "1.9.0" in detail and RUNNING in detail
    # Information, not something a command should repair behind the user.
    assert report.drift == []
    assert report.fixable == []


@pytest.mark.parametrize("recorded", [RUNNING, "3.0.0", "2.0", None, "dev", ""])
def test_only_a_strictly_older_version_is_reported(tmp_path, monkeypatch, recorded):
    config, copy = synced_project(tmp_path, monkeypatch)
    record_version(copy, recorded)

    # The same version is not old, a newer one is a downgrade rather than an
    # outdated copy, and a version that is not a number proves nothing.
    assert kinds(config) == []


def test_version_keys_compare_numbers_not_strings():
    assert _version_key("1.4") == _version_key("1.4.0")
    assert _version_key("1.4.0rc1") == (1, 4)
    assert _version_key("1.20.0") > _version_key("1.4.0")
    assert _version_key("dev") is None
    assert _version_key("") is None


def test_outdated_is_not_drift_and_does_not_fail_strict(tmp_path, monkeypatch):
    config, copy = synced_project(tmp_path, monkeypatch)
    record_version(copy, "1.0.0")

    result = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--json", "--strict"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [item["kind"] for item in payload["findings"]] == [OUTDATED]
    assert payload["counts"]["outdated"] == 1
    assert payload["drift"] == 0
    assert payload["clean"] is False


def test_the_human_report_names_the_section_and_the_versions(tmp_path, monkeypatch):
    config, copy = synced_project(tmp_path, monkeypatch)
    record_version(copy, "1.0.0")

    result = CliRunner().invoke(main, ["doctor", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "Converted by an older skill-bridge" in result.output
    assert "1 outdated" in result.output
    assert "1.0.0" in result.output


def test_fix_leaves_an_outdated_copy_for_sync(tmp_path, monkeypatch):
    config, copy = synced_project(tmp_path, monkeypatch)
    record_version(copy, "1.0.0")
    marker = copy / ".skill-bridge.json"
    before = marker.read_text(encoding="utf-8")

    result = CliRunner().invoke(main, ["doctor", "--config", str(config), "--fix"])

    assert result.exit_code == 0, result.output
    assert marker.read_text(encoding="utf-8") == before
    assert kinds(config) == [OUTDATED]


def test_fix_says_which_copies_it_left_for_sync(tmp_path, monkeypatch):
    config = write_project(tmp_path)
    monkeypatch.setattr(doctor_module, "package_version", lambda: RUNNING)
    # The refreshed copy must record the version doctor compares against, or the
    # repair itself would make it look outdated.
    monkeypatch.setattr(provenance, "package_version", lambda: RUNNING)
    source = tmp_path / "src"
    (source / "other").mkdir()
    (source / "other" / "SKILL.md").write_text(
        SKILL.replace("demo", "other"), encoding="utf-8"
    )
    run_sync(config_path=config)
    record_version(tmp_path / "tgt" / "other", "1.0.0")
    behind = source / "demo" / "SKILL.md"
    behind.write_text(
        behind.read_text(encoding="utf-8") + "\n## Later\n", encoding="utf-8"
    )

    result = CliRunner().invoke(main, ["doctor", "--config", str(config), "--fix"])

    assert result.exit_code == 0, result.output
    assert "1 copy(ies) came from an older skill-bridge" in result.output
    assert kinds(config) == [OUTDATED]


def test_sync_records_the_running_version_and_clears_it(tmp_path, monkeypatch):
    config, copy = synced_project(tmp_path, monkeypatch)
    record_version(copy, "1.0.0")
    assert kinds(config) == [OUTDATED]

    monkeypatch.setattr(provenance, "package_version", lambda: RUNNING)
    run_sync(config_path=config)

    assert kinds(config) == []
    marker = json.loads((copy / ".skill-bridge.json").read_text(encoding="utf-8"))
    assert marker["version"] == RUNNING


def test_a_copy_that_is_behind_reports_only_the_drift(tmp_path, monkeypatch):
    config, copy = synced_project(tmp_path, monkeypatch)
    record_version(copy, "1.0.0")
    source = tmp_path / "src" / "demo" / "SKILL.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n## Later\n", encoding="utf-8"
    )

    # One sync settles both, so the drift is the line worth reading.
    assert kinds(config) == [OUT_OF_SYNC]


def test_a_hand_edited_copy_reports_only_the_edit(tmp_path, monkeypatch):
    config, copy = synced_project(tmp_path, monkeypatch)
    record_version(copy, "1.0.0")
    skill = copy / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "\n<!-- mine -->\n", encoding="utf-8"
    )

    assert kinds(config) == [EDITED]


def test_outdated_is_reported_alongside_a_lossy_field(tmp_path, monkeypatch):
    config = write_project(tmp_path)
    monkeypatch.setattr(doctor_module, "package_version", lambda: RUNNING)
    source = tmp_path / "src" / "demo" / "SKILL.md"
    source.write_text(
        SKILL.replace("description:", "model: opus\ndescription:"), encoding="utf-8"
    )
    run_sync(config_path=config)
    record_version(tmp_path / "tgt" / "demo", "1.0.0")

    # Lossy is a property of the format pair, so it survives any version.
    assert kinds(config) == [OUTDATED, LOSSY]
