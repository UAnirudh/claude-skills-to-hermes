"""Gemini CLI skill exporter (.gemini/skills/)."""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class GeminiCliExporter(PortableSkillExporter):
    format_id = AgentFormat.GEMINI
    display_name = "Gemini CLI"
    default_output_subdir = ".gemini/skills"
    description = "Google Gemini CLI skills; also reads the shared .agents/skills folder"


register_exporter(GeminiCliExporter())
