"""Canonical skill representation and format identifiers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AgentFormat(str, Enum):
    """Supported agent skill target formats."""

    CLAUDE_CODE = "claude-code"
    CURSOR = "cursor"
    HERMES = "hermes"
    CODEX = "codex"
    OPENAGENTS = "openagents"  # agentskills.io standard
    WINDSURF = "windsurf"
    CLINE = "cline"
    ROO = "roo"
    CONTINUE = "continue"
    GENERIC = "generic"

    @classmethod
    def choices(cls) -> list[str]:
        return [f.value for f in cls]

    @classmethod
    def from_string(cls, value: str) -> AgentFormat:
        normalized = value.strip().lower().replace("_", "-")
        aliases = {
            "claude": cls.CLAUDE_CODE,
            "ecc": cls.CLAUDE_CODE,
            "openai": cls.CODEX,
            "openai-codex": cls.CODEX,
            "agentskills": cls.OPENAGENTS,
            "agent-skills": cls.OPENAGENTS,
            "standard": cls.OPENAGENTS,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError as exc:
            valid = ", ".join(cls.choices())
            raise ValueError(f"Unknown format '{value}'. Valid: {valid}") from exc


@dataclass
class SkillBundle:
    """A skill directory with SKILL.md and optional supporting files."""

    root: Path
    name: str
    description: str
    body: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    source_format: AgentFormat | None = None

    def supporting_dirs(self) -> list[str]:
        known = {"scripts", "references", "reference", "assets", "templates", "examples"}
        found: list[str] = []
        if not self.root.is_dir():
            return found
        for child in self.root.iterdir():
            if child.is_dir() and child.name in known:
                found.append(child.name)
        return sorted(found)

    def extra_files(self) -> list[Path]:
        """Markdown and config files at skill root besides SKILL.md."""
        if not self.root.is_dir():
            return []
        skip = {"SKILL.md", "skill.md"}
        return sorted(
            p for p in self.root.iterdir() if p.is_file() and p.name not in skip
        )


@dataclass
class ConversionResult:
    """Output of a single skill conversion."""

    source: Path
    target_format: AgentFormat
    output_dir: Path
    skill_name: str
    warnings: list[str] = field(default_factory=list)
