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
    DEVIN = "devin"
    CLINE = "cline"
    ROO = "roo"
    CONTINUE = "continue"
    OPENCODE = "opencode"
    GEMINI = "gemini"
    COPILOT = "copilot"
    GOOSE = "goose"
    OPENHANDS = "openhands"
    AMP = "amp"
    GENERIC = "generic"

    @classmethod
    def choices(cls) -> list[str]:
        return [f.value for f in cls]

    @classmethod
    def from_string(cls, value: str) -> AgentFormat:
        normalized = value.strip().lower().replace("_", "-")
        aliases = {
            "claude": cls.CLAUDE_CODE,
            "claude-code-cli": cls.CLAUDE_CODE,
            "ecc": cls.CLAUDE_CODE,
            "openai": cls.CODEX,
            "openai-codex": cls.CODEX,
            "codex-cli": cls.CODEX,
            "agentskills": cls.OPENAGENTS,
            "agent-skills": cls.OPENAGENTS,
            "open-agents": cls.OPENAGENTS,
            "standard": cls.OPENAGENTS,
            "portable": cls.OPENAGENTS,
            "windsurf-cascade": cls.WINDSURF,
            "cascade": cls.WINDSURF,
            "devin-desktop": cls.DEVIN,
            "devin-local": cls.DEVIN,
            "continue-dev": cls.CONTINUE,
            "continue.dev": cls.CONTINUE,
            "open-code": cls.OPENCODE,
            "opencode-cli": cls.OPENCODE,
            "gemini-cli": cls.GEMINI,
            "github-copilot": cls.COPILOT,
            "copilot-cli": cls.COPILOT,
            "vscode": cls.COPILOT,
            "vs-code": cls.COPILOT,
            "goose-cli": cls.GOOSE,
            "open-hands": cls.OPENHANDS,
            "amp-cli": cls.AMP,
            "zed": cls.AMP,
            "kilo": cls.AMP,
            "kilo-code": cls.AMP,
            "warp": cls.AMP,
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
