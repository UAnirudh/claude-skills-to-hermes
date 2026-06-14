"""Roo Code skill exporter."""

from __future__ import annotations

from pathlib import Path

from skill_bridge.formats.base import FormatExporter, register_exporter
from skill_bridge.formats.openagents import OpenAgentsExporter
from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle


class RooExporter(FormatExporter):
    format_id = AgentFormat.ROO
    display_name = "Roo Code"
    default_output_subdir = ".roo/skills"
    description = "Roo Code agent skills (SKILL.md standard)"

    def __init__(self) -> None:
        self._base = OpenAgentsExporter()

    def export(
        self,
        bundle: SkillBundle,
        output_root: Path,
        *,
        skill_name: str | None = None,
    ) -> ConversionResult:
        result = self._base.export(bundle, output_root, skill_name=skill_name)
        result.target_format = self.format_id
        return result


register_exporter(RooExporter())
