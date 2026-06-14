"""Windsurf Cascade skills exporter."""

from __future__ import annotations

from pathlib import Path

from skill_bridge.formats.base import FormatExporter, register_exporter
from skill_bridge.formats.openagents import OpenAgentsExporter
from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle


class WindsurfExporter(FormatExporter):
    format_id = AgentFormat.WINDSURF
    display_name = "Windsurf (Cascade)"
    default_output_subdir = ".windsurf/skills"
    description = "Windsurf Cascade skills — agentskills.io compatible layout"

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
        result.warnings.append("Windsurf uses standard SKILL.md; installed to .windsurf/skills/")
        return result


register_exporter(WindsurfExporter())
