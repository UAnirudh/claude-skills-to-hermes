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

SKILL_FILENAMES = ("SKILL.md", "skill.md")

#: Directories that never contain authored skills.
SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


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
    if ".opencode" in parts or ".config" in parts and "opencode" in parts:
        return AgentFormat.OPENCODE
    if ".gemini" in parts:
        return AgentFormat.GEMINI
    if ".copilot" in parts or ".github" in parts:
        return AgentFormat.COPILOT
    if ".goose" in parts or "goose" in parts:
        return AgentFormat.GOOSE
    if ".openhands" in parts:
        return AgentFormat.OPENHANDS
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


def is_skill_dir(path: Path) -> bool:
    return any((path / name).is_file() for name in SKILL_FILENAMES)


def load_skill(skill_dir: Path) -> SkillBundle:
    skill_dir = skill_dir.resolve()
    skill_md = next(
        (skill_dir / name for name in SKILL_FILENAMES if (skill_dir / name).is_file()),
        None,
    )
    if skill_md is None:
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
    root = Path(root).resolve()
    if not root.is_dir():
        return []
    if is_skill_dir(root):
        return [root]

    results: set[Path] = set()
    for filename in SKILL_FILENAMES:
        for skill_md in root.rglob(filename):
            relative_parts = skill_md.relative_to(root).parts
            if any(part in SKIP_DIR_NAMES for part in relative_parts):
                continue
            results.add(skill_md.parent)
    return sorted(results)
