"""OpenCode skill exporter (.opencode/skills/, ~/.config/opencode/skills/)."""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class OpenCodeExporter(PortableSkillExporter):
    format_id = AgentFormat.OPENCODE
    display_name = "OpenCode"
    default_output_subdir = ".opencode/skills"
    global_skill_subdir = ".config/opencode/skills"
    description = "OpenCode skills; also reads .claude/skills and .agents/skills"


register_exporter(OpenCodeExporter())
