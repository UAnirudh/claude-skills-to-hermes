"""OpenAI Codex skill exporter (.agents/skills/)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

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
        write_openai_yaml: bool = True,
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

        if write_openai_yaml:
            allow_implicit = not bool(bundle.frontmatter.get("disable-model-invocation"))
            try:
                agents_dir = output_root.parent / "agents"
                agents_dir.mkdir(parents=True, exist_ok=True)
                openai_yaml = agents_dir / "openai.yaml"
                entry = {
                    "display_name": name.replace("-", " ").title(),
                    "description": bundle.description[:500],
                    "allow_implicit_invocation": allow_implicit,
                }
                if openai_yaml.is_file():
                    data = yaml.safe_load(openai_yaml.read_text(encoding="utf-8")) or {}
                else:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                skills = data.setdefault("skills", {})
                if not isinstance(skills, dict):
                    skills = {}
                    data["skills"] = skills
                skills[name] = entry
                openai_yaml.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
                warnings.append(f"Updated {openai_yaml} with skill entry")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Could not write agents/openai.yaml: {exc}")

        return ConversionResult(bundle.root, self.format_id, dest, name, warnings)


register_exporter(CodexExporter())
