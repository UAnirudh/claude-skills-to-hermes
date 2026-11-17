"""Shared exporter for harnesses that read the standard SKILL.md layout.

A large and growing set of harnesses (Codex, Cursor, Gemini CLI, Copilot,
OpenCode, Cline, Amp, Goose, Roo, …) all read the same directory shape:

    <skills root>/<skill-name>/SKILL.md
    <skills root>/<skill-name>/references/…
    <skills root>/<skill-name>/scripts/…

They differ only in where that root lives, so subclasses set paths only.
"""

from __future__ import annotations

from pathlib import Path

from skill_bridge.formats.base import FormatExporter
from skill_bridge.formats.openagents import build_standard_frontmatter
from skill_bridge.models import ConversionResult, SkillBundle
from skill_bridge.utils import (
    copy_skill_tree,
    normalize_reference_dirs,
    prepare_dest,
    render_skill_md,
    slugify_name,
)


class PortableSkillExporter(FormatExporter):
    """Write a plain, spec-compliant SKILL.md directory for a harness."""

    def export(
        self,
        bundle: SkillBundle,
        output_root: Path,
        *,
        skill_name: str | None = None,
    ) -> ConversionResult:
        name = slugify_name(skill_name or bundle.name)
        dest = prepare_dest(output_root, name)
        copy_skill_tree(bundle.root, dest)
        warnings = normalize_reference_dirs(bundle, dest)

        frontmatter = build_standard_frontmatter(bundle, name)
        (dest / "SKILL.md").write_text(
            render_skill_md(frontmatter, bundle.body), encoding="utf-8"
        )
        return ConversionResult(bundle.root, self.format_id, dest, name, warnings)
