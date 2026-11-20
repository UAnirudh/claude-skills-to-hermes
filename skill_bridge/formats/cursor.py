"""Cursor Agent Skills exporter (.cursor/skills/)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skill_bridge.formats.base import FormatExporter, register_exporter
from skill_bridge.formats.openagents import build_standard_frontmatter
from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle
from skill_bridge.utils import (
    copy_skill_tree,
    normalize_reference_dirs,
    prepare_dest,
    render_skill_md,
    slugify_name,
)


class CursorExporter(FormatExporter):
    format_id = AgentFormat.CURSOR
    display_name = "Cursor"
    default_output_subdir = ".cursor/skills"
    description = "Cursor IDE agent skills with optional disable-model-invocation"

    def export(
        self,
        bundle: SkillBundle,
        output_root: Path,
        *,
        skill_name: str | None = None,
        explicit_only: bool = False,
    ) -> ConversionResult:
        name = slugify_name(skill_name or bundle.name)
        dest = prepare_dest(output_root, name)
        copy_skill_tree(bundle.root, dest)
        warnings = normalize_reference_dirs(bundle, dest)

        fm: dict[str, Any] = build_standard_frontmatter(
            bundle, name, target=self.format_id
        )

        # Cursor-specific: slash-command style skills should not auto-invoke.
        if explicit_only:
            fm["disable-model-invocation"] = True

        (dest / "SKILL.md").write_text(render_skill_md(fm, bundle.body), encoding="utf-8")
        return ConversionResult(bundle.root, self.format_id, dest, name, warnings)


_exporter = CursorExporter()
register_exporter(_exporter)
