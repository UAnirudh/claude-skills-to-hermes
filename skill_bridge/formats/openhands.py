"""OpenHands skill exporter (.openhands/skills/, ~/.openhands/skills/).

Source of truth: docs.openhands.dev/overview/skills. Current docs recommend
``.agents/skills/`` and ``~/.agents/skills/``, and keep ``.openhands/skills/``
(plus ``.openhands/microagents/``) as supported legacy locations, with
``~/.openhands/skills/`` documented as the user-level install target. Existing
installs live there, so this exporter keeps writing them rather than
duplicating skills into the shared ``.agents`` tree.
"""

from __future__ import annotations

from skill_bridge.formats.base import register_exporter
from skill_bridge.formats.portable import PortableSkillExporter
from skill_bridge.models import AgentFormat


class OpenHandsExporter(PortableSkillExporter):
    format_id = AgentFormat.OPENHANDS
    display_name = "OpenHands"
    default_output_subdir = ".openhands/skills"
    description = "OpenHands skills; also reads .agents/skills and legacy microagents"


register_exporter(OpenHandsExporter())
