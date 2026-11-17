"""Amp skill exporter (portable .agents/skills convention).

Amp shares the ``.agents/skills`` folder with Codex, Cursor, Gemini CLI,
Copilot, OpenCode, Cline, Kilo Code, Warp, and Zed. Exports are therefore
portable by construction; ``zed``, ``kilo``, and ``warp`` are accepted as
aliases of this format.
"""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class AmpExporter(PortableSkillExporter):
    format_id = AgentFormat.AMP
    display_name = "Amp (portable .agents)"
    default_output_subdir = ".agents/skills"
    description = "Amp, Zed, Kilo Code, and Warp skills via the shared .agents/skills folder"


register_exporter(AmpExporter())
