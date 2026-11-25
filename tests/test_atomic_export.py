"""Export lands in one step: a failure must never leave a half-written copy.

`sync` and `watch` can die mid-conversion, and `doctor` treats a copy whose
contents differ from its marker as drift — which a half-written directory would
look like forever. Each conversion is therefore staged beside the skills root
and renamed into place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skill_bridge import converter
from skill_bridge.converter import convert_skill
from skill_bridge.models import AgentFormat, ConversionResult


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


def test_a_failing_first_export_leaves_no_skill_behind(tmp_path, monkeypatch):
    source = tmp_path / "src"
    skill = write_skill(source)
    out = tmp_path / "tgt"

    monkeypatch.setattr(converter, "get_exporter", lambda fmt: ExplodingExporter())
    with pytest.raises(RuntimeError):
        convert_skill(skill, AgentFormat.HERMES, out, validate=False)

    assert not (out / "demo").exists()
    assert leftovers(out) == []
