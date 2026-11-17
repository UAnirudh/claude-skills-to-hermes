"""Goose skill exporter (.goose/skills/, ~/.config/goose/skills/)."""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class GooseExporter(PortableSkillExporter):
    format_id = AgentFormat.GOOSE
    display_name = "Goose"
    default_output_subdir = ".goose/skills"
    global_skill_subdir = ".config/goose/skills"
    description = "Block Goose CLI and desktop agent skills"


register_exporter(GooseExporter())
