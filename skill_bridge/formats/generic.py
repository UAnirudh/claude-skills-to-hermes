"""Passthrough generic exporter."""

from __future__ import annotations

from pathlib import Path

from skill_bridge.formats.base import FormatExporter, register_exporter
from skill_bridge.formats.openagents import OpenAgentsExporter
from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle


class GenericExporter(FormatExporter):
    format_id = AgentFormat.GENERIC
    display_name = "Generic"
    default_output_subdir = "skills"
    description = "Normalized agentskills.io output without agent-specific paths"

    def __init__(self) -> None:
        self._base = OpenAgentsExporter()

    def export(
        self,
        bundle: SkillBundle,
        output_root: Path,
        *,
        skill_name: str | None = None,
    ) -> ConversionResult:
        return self._base.export(bundle, output_root, skill_name=skill_name)


register_exporter(GenericExporter())
