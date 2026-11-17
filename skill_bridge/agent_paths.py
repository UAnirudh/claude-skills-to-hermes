"""Default install paths for agent skills on each platform."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from skill_bridge.formats.base import get_exporter, load_exporters
from skill_bridge.models import AgentFormat


def user_home() -> Path:
    return Path.home()


def expand_path(raw: str | Path) -> Path:
    path = Path(os.path.expandvars(str(raw))).expanduser()
    return path.resolve()


def global_skill_root(fmt: AgentFormat) -> Path:
    """User-level skill directory for an agent."""
    load_exporters()
    exporter = get_exporter(fmt)
    subdir = exporter.global_skill_subdir or exporter.default_output_subdir
    return user_home() / Path(subdir)


def project_skill_root(fmt: AgentFormat, project: Path | None = None) -> Path:
    """Project-scoped skill directory."""
    load_exporters()
    subdir = get_exporter(fmt).default_output_subdir
    root = project or Path.cwd()
    return (root / subdir).resolve()


def resolve_skill_path(
    fmt: AgentFormat,
    *,
    scope: str = "global",
    project: Path | None = None,
    override: str | Path | None = None,
) -> Path:
    if override:
        return expand_path(override)
    if scope == "project":
        return project_skill_root(fmt, project)
    return global_skill_root(fmt)


def detect_installed_agents() -> dict[AgentFormat, Path]:
    """Return agent formats whose global skill directories exist."""
    found: dict[AgentFormat, Path] = {}
    for fmt in AgentFormat:
        if fmt == AgentFormat.GENERIC:
            continue
        try:
            path = global_skill_root(fmt)
            if path.is_dir():
                found[fmt] = path
        except KeyError:
            continue
    return found


def platform_label() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"
