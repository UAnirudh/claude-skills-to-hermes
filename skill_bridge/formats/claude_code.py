"""Claude Code skill exporter (~/.claude/skills/)."""

from __future__ import annotations

from pathlib import Path

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


class ClaudeCodeExporter(FormatExporter):
    format_id = AgentFormat.CLAUDE_CODE
    display_name = "Claude Code"
    default_output_subdir = ".claude/skills"
    description = "Anthropic Claude Code personal and project skills"

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

        # Claude Code reads origin/allowed-tools/model/paths/etc. natively, so
        # the shared table already puts every field it understands at top level.
        fm = build_standard_frontmatter(bundle, name, target=self.format_id)

        meta = fm.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("converted-by", "skill-bridge")
        meta.setdefault("source-format", (bundle.source_format or AgentFormat.GENERIC).value)
        fm["metadata"] = meta

        (dest / "SKILL.md").write_text(render_skill_md(fm, bundle.body), encoding="utf-8")
        return ConversionResult(bundle.root, self.format_id, dest, name, warnings)


register_exporter(ClaudeCodeExporter())
