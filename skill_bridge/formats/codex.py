"""OpenAI Codex skill exporter (.agents/skills/)."""

from __future__ import annotations

from pathlib import Path

from skill_bridge.formats.base import FormatExporter, register_exporter
from skill_bridge.formats.openagents import build_standard_frontmatter
from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle
from skill_bridge.utils import copy_skill_tree, normalize_reference_dirs, render_skill_md, slugify_name


class CodexExporter(FormatExporter):
    format_id = AgentFormat.CODEX
    display_name = "OpenAI Codex"
    default_output_subdir = ".agents/skills"
    description = "OpenAI Codex repo and global skills under .agents/skills"

    def export(
        self,
        bundle: SkillBundle,
        output_root: Path,
        *,
        skill_name: str | None = None,
    ) -> ConversionResult:
        name = slugify_name(skill_name or bundle.name)
        dest = output_root / name
        if dest.exists():
            import shutil

            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        copy_skill_tree(bundle.root, dest)
        warnings = normalize_reference_dirs(bundle, dest)

        fm = build_standard_frontmatter(bundle, name)
        # Strip Cursor-only fields
        fm.pop("disable-model-invocation", None)

        meta = fm.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("codex", {"source": "skill-bridge"})
        fm["metadata"] = meta

        (dest / "SKILL.md").write_text(render_skill_md(fm, bundle.body), encoding="utf-8")

        return ConversionResult(bundle.root, self.format_id, dest, name, warnings)


register_exporter(CodexExporter())
