"""Parse SKILL.md files from any supported agent."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from skill_bridge.models import AgentFormat, SkillBundle


FRONTMATTER_RE = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?",
    re.DOTALL,
)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.lstrip("\ufeff")
    raw_yaml = match.group(1)
    body = text[match.end() :]
    data = yaml.safe_load(raw_yaml) or {}
    if not isinstance(data, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML mapping")
    return data, body


def detect_source_format(path: Path, frontmatter: dict[str, Any]) -> AgentFormat:
    parts = {p.lower() for p in path.parts}
    if ".cursor" in parts or "skills-cursor" in parts:
        return AgentFormat.CURSOR
    if ".claude" in parts:
        return AgentFormat.CLAUDE_CODE
    if ".agents" in parts:
        return AgentFormat.CODEX
    if ".hermes" in parts or ".hermes-agent" in parts:
        return AgentFormat.HERMES
    if ".windsurf" in parts or "windsurf" in parts:
        return AgentFormat.WINDSURF
    if ".cline" in parts:
        return AgentFormat.CLINE
    if ".roo" in parts:
        return AgentFormat.ROO
    if ".continue" in parts:
        return AgentFormat.CONTINUE

    metadata = frontmatter.get("metadata") or {}
    if isinstance(metadata, dict) and "hermes" in metadata:
        return AgentFormat.HERMES
    if frontmatter.get("disable-model-invocation") is not None:
        return AgentFormat.CURSOR
    if frontmatter.get("origin") == "ECC":
        return AgentFormat.CLAUDE_CODE
    return AgentFormat.GENERIC


def load_skill(skill_dir: Path) -> SkillBundle:
    skill_dir = skill_dir.resolve()
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        skill_md = skill_dir / "skill.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"No SKILL.md found in {skill_dir}")

    text = skill_md.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)

    name = str(frontmatter.get("name") or skill_dir.name).strip()
    description = str(frontmatter.get("description") or "").strip()
    if not description:
        # Infer from first heading or first paragraph
        heading = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if heading:
            description = heading.group(1).strip()
        else:
            first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
            description = first_line[:200] if first_line else f"Skill: {name}"

    return SkillBundle(
        root=skill_dir,
        name=name,
        description=description,
        body=body,
        frontmatter=frontmatter,
        source_format=detect_source_format(skill_dir, frontmatter),
    )


def find_skills(root: Path) -> list[Path]:
    """Discover skill directories containing SKILL.md under root."""
    root = root.resolve()
    if (root / "SKILL.md").is_file() or (root / "skill.md").is_file():
        return [root]

    results: list[Path] = []
    for skill_md in root.rglob("SKILL.md"):
        if any(part.startswith(".") and part not in {".cursor", ".claude", ".agents"} for part in skill_md.parent.parts):
            # Skip hidden dirs except known agent folders
            parent_name = skill_md.parent.name
            if parent_name.startswith(".") and parent_name not in {".cursor", ".claude", ".agents"}:
                continue
        results.append(skill_md.parent)
    return sorted(set(results))
