"""Devin Desktop (Devin Local) skill exporter.

Source of truth: docs.devin.ai/desktop/devin-local/customizations — Windsurf is
now Devin Desktop, and docs.windsurf.com redirects there. Workspace skills live
in ``.devin/skills/<name>/`` and global skills in ``~/.config/devin/skills/<name>/``.
The docs also read ``.agents/skills`` / ``~/.agents/skills`` and the legacy
``.windsurf/skills`` folder, which the separate ``windsurf`` format still writes,
so skills converted for either layout keep working.
"""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class DevinExporter(PortableSkillExporter):
    format_id = AgentFormat.DEVIN
    display_name = "Devin Desktop"
    default_output_subdir = ".devin/skills"
    global_skill_subdir = ".config/devin/skills"
    description = (
        "Devin Desktop / Devin Local skills; also reads .agents/skills "
        "and legacy .windsurf/skills"
    )


register_exporter(DevinExporter())
