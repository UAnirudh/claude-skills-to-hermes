"""Windsurf Cascade skills exporter.

Source of truth: docs.windsurf.com/windsurf/cascade/skills.
Workspace skills live in ``.windsurf/skills/``, global skills in
``~/.codeium/windsurf/skills/``. Windsurf also discovers ``.agents/skills`` and
``~/.agents/skills`` (plus ``.claude/skills`` when Claude Code config reading is
enabled), so output here is readable by the shared-convention harnesses too.
"""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class WindsurfExporter(PortableSkillExporter):
    format_id = AgentFormat.WINDSURF
    display_name = "Windsurf (Cascade)"
    default_output_subdir = ".windsurf/skills"
    #: Windsurf keeps user-level config under ~/.codeium/windsurf.
    global_skill_subdir = ".codeium/windsurf/skills"
    description = "Windsurf Cascade skills; also reads .agents/skills and .claude/skills"


register_exporter(WindsurfExporter())
