"""Hermes Agent skill exporter with metadata.hermes block."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from skill_bridge.compat import CARRIER
from skill_bridge.formats.base import FormatExporter, register_exporter
from skill_bridge.formats.openagents import build_standard_frontmatter
from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle
from skill_bridge.utils import (
    copy_skill_tree,
    deep_merge,
    get_metadata_block,
    infer_tags,
    normalize_reference_dirs,
    render_skill_md,
    slugify_name,
)


def build_hermes_frontmatter(bundle: SkillBundle, name: str) -> dict[str, Any]:
    # The shared table already translated foreign fields: source-only extras
    # arrive under metadata['skill-bridge'] and are merged below, never lost.
    fm = build_standard_frontmatter(bundle, name, target=AgentFormat.HERMES)

    for key in ("version", "license", "platforms", "compatibility"):
        if key in bundle.frontmatter:
            fm[key] = bundle.frontmatter[key]

    if "platforms" not in fm:
        # Hermes accepts macos, linux, windows — omit for all platforms
        pass

    existing_meta = get_metadata_block(bundle)
    # `build_standard_frontmatter` already translated the carrier into `fm`.
    # Merging the source's copy back in would park fields Hermes does read
    # (its `version`, say) a second time, next to the top-level key.
    existing_meta.pop(CARRIER, None)
    hermes_meta: dict[str, Any] = {}

    if isinstance(existing_meta.get("hermes"), dict):
        hermes_meta = dict(existing_meta["hermes"])

    if "tags" not in hermes_meta:
        hermes_meta["tags"] = infer_tags(bundle)

    # Map Cursor explicit-only to Hermes-friendly category hint
    if bundle.frontmatter.get("disable-model-invocation"):
        hermes_meta.setdefault("category", "workflow")

    carried = fm.get("metadata")
    carried = dict(carried) if isinstance(carried, dict) else {}
    merged_meta = deep_merge(carried, existing_meta)
    merged_meta = deep_merge(merged_meta, {"hermes": hermes_meta})
    merged_meta.setdefault("converted-by", "skill-bridge")
    fm["metadata"] = merged_meta

    return fm


def ensure_hermes_sections(body: str) -> tuple[str, list[str]]:
    """Add recommended Hermes sections if missing."""
    warnings: list[str] = []
    lower = body.lower()
    additions: list[str] = []

    if "## when to use" not in lower and "## when to activate" not in lower:
        additions.append(
            "## When to Use\n\n"
            "Use this skill when the task matches the description in the frontmatter "
            "or when the user explicitly requests this workflow.\n"
        )
        warnings.append("Added '## When to Use' section (Hermes recommendation)")

    if "## procedure" not in lower and "## instructions" not in lower and "## workflow" not in lower:
        # Body likely already has steps — don't duplicate heavily
        pass

    if not additions:
        return body, warnings
    return body.rstrip() + "\n\n" + "\n".join(additions), warnings


class HermesExporter(FormatExporter):
    format_id = AgentFormat.HERMES
    display_name = "Hermes Agent"
    default_output_subdir = ".hermes/skills"
    description = "NousResearch Hermes Agent with metadata.hermes tags and toolsets"

    def export(
        self,
        bundle: SkillBundle,
        output_root: Path,
        *,
        skill_name: str | None = None,
        add_sections: bool = True,
    ) -> ConversionResult:
        name = slugify_name(skill_name or bundle.name)
        dest = output_root / name
        if dest.exists():
            import shutil

            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        copy_skill_tree(bundle.root, dest)
        warnings = normalize_reference_dirs(bundle, dest)

        body = bundle.body
        if add_sections:
            body, section_warnings = ensure_hermes_sections(body)
            warnings.extend(section_warnings)

        fm = build_hermes_frontmatter(bundle, name)
        (dest / "SKILL.md").write_text(render_skill_md(fm, body), encoding="utf-8")

        # Hermes skills can ship a config stub when metadata.hermes.config exists
        hermes = (fm.get("metadata") or {}).get("hermes") or {}
        if isinstance(hermes, dict) and hermes.get("config"):
            config_path = dest / "config.yaml"
            if not config_path.exists():
                config_path.write_text(
                    yaml.safe_dump({"settings": {}}, sort_keys=False),
                    encoding="utf-8",
                )
                warnings.append("Created placeholder config.yaml for Hermes config metadata")

        return ConversionResult(bundle.root, self.format_id, dest, name, warnings)


register_exporter(HermesExporter())
