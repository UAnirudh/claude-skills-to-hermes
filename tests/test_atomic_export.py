"""Export lands in one step: a failure must never leave a half-written copy.

`sync` and `watch` can die mid-conversion, and `doctor` treats a copy whose
contents differ from its marker as drift — which a half-written directory would
look like forever. Each conversion is therefore staged beside the skills root
and renamed into place.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from skill_bridge import converter
from skill_bridge.config import SkillLocation, SyncProfile
from skill_bridge.converter import convert_skill, sweep_stale_holders
from skill_bridge.doctor import diagnose
from skill_bridge.models import AgentFormat, ConversionResult
from skill_bridge.sync import sync_profile


def write_skill(root: Path, name: str = "demo", body: str = "# Demo\n") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name} used by the atomic export tests.\n"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )
    return skill_dir


def leftovers(out: Path) -> list[Path]:
    """Staging directories the converter failed to clean up, if any."""
    return sorted(out.parent.glob(".skill-bridge-tmp-*"))


class ExplodingExporter:
    """Writes a partial skill, then fails like a killed process would."""

    def __init__(self, raise_after_write: bool = True) -> None:
        self.raise_after_write = raise_after_write

    def export(self, bundle, output_root, *, skill_name=None) -> ConversionResult:
        dest = Path(output_root) / (skill_name or "demo")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text("half written", encoding="utf-8")
        if self.raise_after_write:
            raise RuntimeError("conversion interrupted")
        return ConversionResult(
            source=bundle.root,
            target_format=AgentFormat.HERMES,
            output_dir=dest,
            skill_name=dest.name,
        )


def test_a_second_conversion_replaces_the_copy_and_leaves_no_temp_dirs(tmp_path):
    source = tmp_path / "src"
    skill = write_skill(source, body="# First\n")
    out = tmp_path / "tgt"

    first = convert_skill(skill, AgentFormat.HERMES, out, validate=False)
    assert first.output_dir == out / "demo"

    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Updated.\n---\n\n# Second\n",
        encoding="utf-8",
    )
    second = convert_skill(skill, AgentFormat.HERMES, out, validate=False)

    assert second.output_dir == out / "demo"
    assert "# Second" in (out / "demo" / "SKILL.md").read_text(encoding="utf-8")
    assert leftovers(out) == []


def test_a_failing_export_keeps_the_previous_copy_intact(tmp_path, monkeypatch):
    source = tmp_path / "src"
    skill = write_skill(source, body="# First\n")
    out = tmp_path / "tgt"
    convert_skill(skill, AgentFormat.HERMES, out, validate=False)
    before = (out / "demo" / "SKILL.md").read_text(encoding="utf-8")

    monkeypatch.setattr(converter, "get_exporter", lambda fmt: ExplodingExporter())
    with pytest.raises(RuntimeError):
        convert_skill(skill, AgentFormat.HERMES, out, validate=False)

    # The old copy is still whole, and no staging directory survived.
    assert (out / "demo" / "SKILL.md").read_text(encoding="utf-8") == before
    assert leftovers(out) == []


def age(path: Path, days: float) -> None:
    stamp = time.time() - days * 24 * 60 * 60
    os.utime(path, (stamp, stamp))


def test_the_sweep_removes_only_old_staging_holders(tmp_path):
    out = tmp_path / "tgt"
    out.mkdir()
    old = tmp_path / ".skill-bridge-tmp-old"
    fresh = tmp_path / ".skill-bridge-tmp-fresh"
    unrelated = tmp_path / "keep-me"
    for directory in (old, fresh, unrelated):
        directory.mkdir()
        (directory / "SKILL.md").write_text("staged", encoding="utf-8")
    age(old, 2)

    removed = sweep_stale_holders(out)

    assert removed == [old]
    assert not old.exists()
    # A fresh holder may belong to a sync that is still running.
    assert (fresh / "SKILL.md").is_file()
    assert unrelated.is_dir()


def test_the_sweep_never_follows_a_symlinked_holder(tmp_path):
    out = tmp_path / "tgt"
    out.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / ".skill-bridge-tmp-link"
    try:
        link.symlink_to(elsewhere)
    except OSError:  # pragma: no cover - Windows without symlink rights
        pytest.skip("symlinks unavailable on this platform")
    age(elsewhere, 3)

    assert sweep_stale_holders(out) == []

    assert link.is_symlink()
    assert elsewhere.is_dir()


def test_sync_sweeps_a_holder_left_by_a_killed_run(tmp_path):
    source = tmp_path / "src"
    write_skill(source)
    target = tmp_path / "tgt"
    crashed = tmp_path / ".skill-bridge-tmp-crashed"
    crashed.mkdir()
    (crashed / "demo").mkdir()
    age(crashed, 2)
    profile = SyncProfile(
        name="test",
        source=SkillLocation(format=AgentFormat.GENERIC, path=source),
        targets=[SkillLocation(format=AgentFormat.HERMES, path=target)],
    )

    results = sync_profile(profile)

    assert [result.skill_name for result in results] == ["demo"]
    assert not crashed.exists()
    assert (target / "demo" / "SKILL.md").is_file()


def test_doctor_sweeps_a_holder_left_by_a_killed_run(tmp_path):
    target = tmp_path / "tgt"
    target.mkdir()
    crashed = tmp_path / ".skill-bridge-tmp-crashed"
    crashed.mkdir()
    age(crashed, 2)

    diagnose([SkillLocation(format=AgentFormat.HERMES, path=target)])

    assert not crashed.exists()


def test_a_failing_first_export_leaves_no_skill_behind(tmp_path, monkeypatch):
    source = tmp_path / "src"
    skill = write_skill(source)
    out = tmp_path / "tgt"

    monkeypatch.setattr(converter, "get_exporter", lambda fmt: ExplodingExporter())
    with pytest.raises(RuntimeError):
        convert_skill(skill, AgentFormat.HERMES, out, validate=False)

    assert not (out / "demo").exists()
    assert leftovers(out) == []
