"""Roo Code skill exporter."""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class RooExporter(PortableSkillExporter):
    format_id = AgentFormat.ROO
    display_name = "Roo Code"
    default_output_subdir = ".roo/skills"
    description = "Roo Code agent skills (SKILL.md standard)"


register_exporter(RooExporter())
