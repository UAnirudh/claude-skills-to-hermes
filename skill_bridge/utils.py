"""Shared utilities for skill conversion."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from skill_bridge.models import SkillBundle


def slugify_name(raw: str) -> str:
    slug = raw.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:64] or "unnamed-skill"


def ordered_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Deterministic key order: name, description, then the rest sorted.

    Conversions can reach the same frontmatter through different hops, so the
    insertion order of ``metadata`` differs. Sorting here is what keeps
    repeated conversions byte-identical.
    """
    ordered: dict[str, Any] = {}
    for key in ("name", "description"):
        if key in data:
            ordered[key] = data[key]
    for key in sorted(k for k in data if k not in ordered):
        value = data[key]
        ordered[key] = ordered_mapping(value) if isinstance(value, dict) else value
    return ordered


def dump_frontmatter(data: dict[str, Any]) -> str:
    return yaml.safe_dump(
        ordered_mapping(data), sort_keys=False, allow_unicode=True
    ).strip()


def prepare_dest(output_root: Path, name: str) -> Path:
    """Create a clean destination directory for a converted skill."""
    dest = output_root / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    return dest


def render_skill_md(frontmatter: dict[str, Any], body: str) -> str:
    body = body.lstrip("\n")
    if not body.endswith("\n"):
        body += "\n"
    return f"---\n{dump_frontmatter(frontmatter)}\n---\n\n{body}"


def copy_skill_tree(
    source: Path,
    dest: Path,
    *,
    exclude: set[str] | None = None,
) -> None:
    exclude = exclude or set()
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name in exclude or item.name.lower() == "skill.md":
            continue
        if item.name == "SKILL.md":
            continue
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def normalize_reference_dirs(bundle: SkillBundle, dest: Path) -> list[str]:
    """Ensure reference materials live under references/ per spec."""
    warnings: list[str] = []
    ref_dir = dest / "references"
    legacy = dest / "reference"
    if legacy.is_dir() and not ref_dir.exists():
        legacy.rename(ref_dir)
        warnings.append("Renamed reference/ → references/ for agentskills.io compatibility")
    return warnings


def infer_tags(bundle: SkillBundle) -> list[str]:
    tags: set[str] = set()
    text = f"{bundle.name} {bundle.description} {bundle.body[:500]}".lower()
    keyword_map = {
        "python": ["python", "django", "fastapi", "pytest"],
        "javascript": ["javascript", "typescript", "react", "node"],
        "rust": ["rust", "cargo"],
        "go": [" golang", " go ", "go mod"],
        "security": ["security", "vulnerability", "owasp", "xss", "sql injection"],
        "testing": ["test", "tdd", "coverage", "e2e"],
        "git": ["git", "commit", "pull request", "merge"],
        "devops": ["docker", "kubernetes", "ci/cd", "deploy"],
        "review": ["code review", "review pr", "audit"],
    }
    for tag, keywords in keyword_map.items():
        if any(k in text for k in keywords):
            tags.add(tag)
    if bundle.source_format:
        tags.add(bundle.source_format.value)
    return sorted(tags)[:8]


def get_metadata_block(bundle: SkillBundle) -> dict[str, Any]:
    meta = bundle.frontmatter.get("metadata")
    return dict(meta) if isinstance(meta, dict) else {}

def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
