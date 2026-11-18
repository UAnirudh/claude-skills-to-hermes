"""Goose skill exporter (.goose/skills/, ~/.config/goose/skills/).

Source of truth: goose-docs.ai/docs/guides/context-engineering/using-skills.
Current docs name ``.agents/skills/`` and ``~/.agents/skills/`` as the
recommended standard, then list ``.goose/skills/``, ``.claude/skills/``,
``~/.claude/skills/`` and the platform config directory (``~/.config/goose/skills``
here) as still discovered. Goose's own folders are what existing installs
contain, so writing them keeps those skills updating instead of duplicating
them into the shared ``.agents`` tree.
"""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class GooseExporter(PortableSkillExporter):
    format_id = AgentFormat.GOOSE
    display_name = "Goose"
    default_output_subdir = ".goose/skills"
    global_skill_subdir = ".config/goose/skills"
    description = (
        "Block Goose skills; also discovers .agents/skills and .claude/skills"
    )


register_exporter(GooseExporter())
