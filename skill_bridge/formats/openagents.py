"""agentskills.io standard exporter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skill_bridge.formats.base import FormatExporter, register_exporter
from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle
from skill_bridge.utils import (
    copy_skill_tree,
    normalize_reference_dirs,
    render_skill_md,
    slugify_name,
)

STANDARD_KEYS = (
    "license",
    "compatibility",
    "allowed-tools",
)


def build_standard_frontmatter(bundle: SkillBundle, name: str) -> dict[str, Any]:
    fm: dict[str, Any] = {"name": name, "description": bundle.description[:1024]}
    for key in STANDARD_KEYS:
        if key in bundle.frontmatter:
            fm[key] = bundle.frontmatter[key]

    # Carry over any remaining frontmatter verbatim, in a fixed order. Harnesses
    # ignore keys they do not know about, and dropping them here is what made
    # conversions between some harnesses lossy (e.g. Claude Code's `origin`).
    # The deterministic order keeps repeated conversions byte-identical.
    for key in sorted(bundle.frontmatter):
        if key in fm or key in {"name", "description", "metadata"}:
            continue
        value = bundle.frontmatter[key]
        if value is None:
            continue
        fm[key] = value

    meta = bundle.frontmatter.get("metadata")
    if isinstance(meta, dict) and meta:
        fm["metadata"] = dict(meta)
    return fm


class OpenAgentsExporter(FormatExporter):
    format_id = AgentFormat.OPENAGENTS
    display_name = "Agent Skills (agentskills.io)"
    default_output_subdir = "skills"
    description = "Open standard format adopted by Claude Code, Codex, Hermes, and others"

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
        (dest / "SKILL.md").write_text(render_skill_md(fm, bundle.body), encoding="utf-8")
        return ConversionResult(bundle.root, self.format_id, dest, name, warnings)


register_exporter(OpenAgentsExporter())
