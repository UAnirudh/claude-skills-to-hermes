"""Cline skill exporter."""

from __future__ import annotations

from pathlib import Path

from skill_bridge.formats.base import FormatExporter, register_exporter
from skill_bridge.formats.openagents import OpenAgentsExporter
from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle


class ClineExporter(FormatExporter):
    format_id = AgentFormat.CLINE
    display_name = "Cline"
    default_output_subdir = ".cline/skills"
    description = "Cline VS Code extension skills (SKILL.md standard)"

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


register_exporter(ClineExporter())
