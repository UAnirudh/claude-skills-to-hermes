"""Reverse conversion and profile-based sync."""

from __future__ import annotations

from pathlib import Path

from skill_bridge.agent_paths import resolve_skill_path
from skill_bridge.config import BridgeConfig, SyncProfile, load_config
from skill_bridge.converter import convert_skill
from skill_bridge.formats.base import get_exporter, load_exporters
from skill_bridge.models import AgentFormat, ConversionResult
from skill_bridge.parser import find_skills

load_exporters()


def reverse_convert(
    source_format: AgentFormat,
    target_format: AgentFormat,
    *,
    source_path: Path | None = None,
    target_path: Path | None = None,
    scope: str = "global",
    project: Path | None = None,
    validate: bool = True,
) -> list[ConversionResult]:
    """
    Convert all skills from one agent's install directory to another.

    Example: Hermes ~/.hermes/skills -> Claude Code ~/.claude/skills
    """
    src_root = resolve_skill_path(source_format, scope=scope, project=project, override=source_path)
    dst_root = resolve_skill_path(target_format, scope=scope, project=project, override=target_path)

    if not src_root.is_dir():
        raise FileNotFoundError(f"Source skills directory not found: {src_root}")

    skills = find_skills(src_root)
    if not skills:
        raise FileNotFoundError(f"No skills found in {src_root}")

    results: list[ConversionResult] = []
    for skill_dir in skills:
        results.append(convert_skill(skill_dir, target_format, dst_root, validate=validate))
    return results


def sync_profile(
    profile: SyncProfile,
    *,
    skill_name: str | None = None,
    allow_empty: bool = False,
) -> list[ConversionResult]:
    """Convert source skills to all targets in a profile.

    With ``allow_empty`` a source that holds no skills yet returns an empty
    result instead of raising, which is what long-running watchers want.
    """
    src = profile.source.path
    if not src.is_dir():
        raise FileNotFoundError(f"Source path missing: {src}")

    if skill_name:
        skill_dirs = [src / skill_name]
        if not (skill_dirs[0] / "SKILL.md").is_file():
            raise FileNotFoundError(f"Skill '{skill_name}' not found under {src}")
    else:
        skill_dirs = find_skills(src)
        if not skill_dirs and not allow_empty:
            raise FileNotFoundError(f"No skills under {src}")

    results: list[ConversionResult] = []
    for skill_dir in skill_dirs:
        for target in profile.targets:
            results.append(
                convert_skill(
                    skill_dir,
                    target.format,
                    target.path,
                    validate=profile.validate,
                )
            )
    return results


def run_sync(
    profile_name: str | None = None,
    config_path: Path | None = None,
    *,
    skill_name: str | None = None,
) -> list[ConversionResult]:
    config = load_config(config_path)
    profile = config.get_profile(profile_name)
    return sync_profile(profile, skill_name=skill_name)
