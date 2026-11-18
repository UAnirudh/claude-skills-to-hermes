"""Reverse conversion and profile-based sync."""

from __future__ import annotations

import os
from pathlib import Path

from skill_bridge.agent_paths import detect_installed_agents, resolve_skill_path
from skill_bridge.config import BridgeConfig, SkillLocation, SyncProfile, load_config
from skill_bridge.converter import convert_skill
from skill_bridge.formats.base import load_exporters
from skill_bridge.models import AgentFormat, ConversionResult
from skill_bridge.parser import find_skills, is_skill_dir
from skill_bridge.utils import slugify_name

load_exporters()


def profile_sources(profile: SyncProfile, *, project: Path | None = None) -> list[SkillLocation]:
    """Resolve a profile's source harnesses, expanding ``sources: auto``."""
    if profile.auto_sources:
        target_formats = {t.format for t in profile.targets}
        scope = profile.source_scope
        return [
            SkillLocation(format=fmt, path=path, scope=scope)
            for fmt, path in sorted(
                detect_installed_agents(scope=scope, project=project).items(),
                key=lambda kv: kv[0].value,
            )
            if fmt not in target_formats
        ]
    if profile.sources:
        return list(profile.sources)
    return [profile.source] if profile.source else []


def collect_source_skills(
    sources: list[SkillLocation],
    *,
    skill_name: str | None = None,
) -> list[tuple[SkillLocation, Path]]:
    """Discover skills across sources, de-duplicated by path and skill name."""
    entries: list[tuple[SkillLocation, Path]] = []
    seen_paths: set[str] = set()
    seen_names: set[str] = set()

    for loc in sources:
        root = loc.path
        if not root.is_dir():
            continue
        if skill_name:
            candidate = root / skill_name
            skill_dirs = [candidate] if is_skill_dir(candidate) else []
        else:
            skill_dirs = find_skills(root)

        for skill_dir in skill_dirs:
            real = os.path.realpath(skill_dir)
            if real in seen_paths:
                continue
            seen_paths.add(real)
            slug = slugify_name(skill_dir.name)
            if slug in seen_names:
                continue
            seen_names.add(slug)
            entries.append((loc, skill_dir))
    return entries


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

    same_root = os.path.realpath(src_root) == os.path.realpath(dst_root)

    results: list[ConversionResult] = []
    for skill_dir in skills:
        if same_root and source_format == target_format:
            continue
        results.append(convert_skill(skill_dir, target_format, dst_root, validate=validate))
    return results


def sync_profile(
    profile: SyncProfile,
    *,
    skill_name: str | None = None,
) -> list[ConversionResult]:
    """Convert source skills (one or many harnesses) to all targets in a profile."""
    sources = profile_sources(profile)
    if not sources:
        raise FileNotFoundError(
            "No source harness found. Install or author skills somewhere first, "
            "or point 'sources' at a directory in skill-bridge.yaml"
        )

    entries = collect_source_skills(sources, skill_name=skill_name)
    if not entries:
        if skill_name:
            listed = ", ".join(str(s.path) for s in sources)
            raise FileNotFoundError(f"Skill '{skill_name}' not found in: {listed}")
        listed = ", ".join(str(s.path) for s in sources)
        raise FileNotFoundError(f"No skills under: {listed}")

    results: list[ConversionResult] = []
    for loc, skill_dir in entries:
        for target in profile.targets:
            if (
                target.format == loc.format
                and os.path.realpath(target.path) == os.path.realpath(skill_dir.parent)
            ):
                continue
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
    config: BridgeConfig = load_config(config_path)
    profile = config.get_profile(profile_name)
    return sync_profile(profile, skill_name=skill_name)
