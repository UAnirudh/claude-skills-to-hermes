"""GitHub Copilot skill exporter (.github/skills/, ~/.copilot/skills/)."""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class CopilotExporter(PortableSkillExporter):
    format_id = AgentFormat.COPILOT
    display_name = "GitHub Copilot"
    default_output_subdir = ".github/skills"
    global_skill_subdir = ".copilot/skills"
    description = "Copilot coding agent, code review, CLI, and VS Code agent mode"


register_exporter(CopilotExporter())
