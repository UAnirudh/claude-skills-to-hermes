"""OpenClaw skill exporter."""

from __future__ import annotations

from pathlib import Path

from skill_bridge.formats.base import FormatExporter, register_exporter
from skill_bridge.formats.openagents import OpenAgentsExporter
from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle


class OpenClawExporter(FormatExporter):
    format_id = AgentFormat.OPENCLAW
    display_name = "OpenClaw"
    default_output_subdir = "skills"
    global_output_subdir = ".openclaw/skills"
    description = "OpenClaw skills (workspace skills/ or ~/.openclaw/skills)"

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


register_exporter(OpenClawExporter())
