"""Cline skill exporter.

Source of truth: docs.cline.bot/customization/skills (and the cline/cline repo).
Project skills live in ``.cline/skills/`` (recommended), global skills in
``~/.cline/skills/``. Cline additionally reads ``.clinerules/skills/`` and
``.claude/skills/`` for compatibility, so Claude-authored skills are picked up
without an extra copy.
"""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class ClineExporter(PortableSkillExporter):
    format_id = AgentFormat.CLINE
    display_name = "Cline"
    default_output_subdir = ".cline/skills"
    description = "Cline (VS Code extension); also reads .clinerules/skills and .claude/skills"


register_exporter(ClineExporter())
