"""OpenHands skill exporter (.openhands/skills/)."""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class OpenHandsExporter(PortableSkillExporter):
    format_id = AgentFormat.OPENHANDS
    display_name = "OpenHands"
    default_output_subdir = ".openhands/skills"
    description = "OpenHands agent skills"


register_exporter(OpenHandsExporter())
