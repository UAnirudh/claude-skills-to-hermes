"""Windsurf Cascade skills exporter.

Source of truth: docs.devin.ai/desktop/cascade/skills (Windsurf is now Devin
Desktop; docs.windsurf.com redirects there). Workspace skills live in
``.windsurf/skills/``, global skills in ``~/.codeium/windsurf/skills/`` — both
still read, and ``~/.codeium`` is unchanged per the Devin Desktop FAQ. Newer
builds prefer ``.devin/skills`` and ``~/.config/devin/skills`` — that pair is the
separate ``devin`` format — and also read ``.agents/skills`` / ``~/.agents/skills``
(plus ``.claude/skills`` when Claude Code config reading is enabled), so output
here is readable by the shared-convention harnesses too.
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
