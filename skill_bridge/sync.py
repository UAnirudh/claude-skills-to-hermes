"""Reverse conversion and profile-based sync."""

from __future__ import annotations

import os
from pathlib import Path

from skill_bridge.agent_paths import detect_installed_agents, resolve_skill_path
from skill_bridge.config import BridgeConfig, SkillLocation, SyncProfile, load_config
from skill_bridge.converter import convert_skill, sweep_stale_holders
from skill_bridge.formats.base import load_exporters
from skill_bridge.models import AgentFormat, ConversionResult
from skill_bridge.parser import find_skills, load_skill
from skill_bridge.prune import (
    PruneOutcome,
    apply_removals,
    plan_removals,
    source_disappeared,
)
from skill_bridge.utils import slugify_name

load_exporters()


def _detected_sources(
    scope: str, project: Path | None
) -> list[tuple[AgentFormat, Path, str]]:
    """Every harness directory in ``scope``; ``both`` keeps both copies.

    A harness can differ between scopes (a global Cursor install plus a
    project-only one), so both paths are kept instead of letting one shadow
    the other.
    """
    scopes = ["global", "project"] if scope == "both" else [scope]
    found: list[tuple[AgentFormat, Path, str]] = []
    seen: set[str] = set()
    for one_scope in scopes:
        detected = detect_installed_agents(scope=one_scope, project=project)
        for fmt, path in sorted(detected.items(), key=lambda kv: kv[0].value):
            key = os.path.realpath(path)
            if key in seen:
                continue
            seen.add(key)
            found.append((fmt, path, one_scope))
    return found


def profile_sources(profile: SyncProfile, *, project: Path | None = None) -> list[SkillLocation]:
    """Resolve a profile's source harnesses, expanding ``sources: auto``."""
    if profile.auto_sources:
        target_formats = {t.format for t in profile.targets}
        # A single-target profile imports from every *other* harness. A
        # multi-target profile (`install --all`) has no other harnesses left,
        # so each target is a source for the others instead; sync_profile still
        # skips writing a skill into the directory it came from.
        skip_targets = len(profile.targets) <= 1
        return [
            SkillLocation(format=fmt, path=path, scope=one_scope)
            for fmt, path, one_scope in _detected_sources(profile.source_scope, project)
            if not (skip_targets and fmt in target_formats)
        ]
    if profile.sources:
        return list(profile.sources)
    return [profile.source] if profile.source else []


def _skills_matching(root: Path, skill_name: str) -> list[Path]:
    """Skill directories ``root`` holds for a name or alias like ``My Skill``.

    A skill is named by its frontmatter ``name``, which need not match its
    folder: ``sync --skill my-fancy-skill`` must find ``My Fancy Skill/``.
    """
    wanted = slugify_name(skill_name)
    matched: list[Path] = []
    for skill_dir in find_skills(root):
        if slugify_name(skill_dir.name) == wanted:
            matched.append(skill_dir)
            continue
        try:
            if slugify_name(load_skill(skill_dir).name) == wanted:
                matched.append(skill_dir)
        except (OSError, ValueError):
            continue
    return matched


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
            skill_dirs = _skills_matching(root, skill_name)
        else:
            skill_dirs = find_skills(root)

        for skill_dir in skill_dirs:
            real = os.path.realpath(skill_dir)
            if real in seen_paths:
                continue
            seen_paths.add(real)
            if source_disappeared(skill_dir):
                # A copy whose source is gone must not feed the sync back into
                # another harness (which would resurrect a deleted skill).
                # `sync --prune` deletes it instead.
                continue
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
    # A run killed outright cannot clean up the directory it was staging in.
    for target in profile.targets:
        sweep_stale_holders(target.path)

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


def run_prune(
    profile_name: str | None = None,
    config_path: Path | None = None,
    *,
    skill_name: str | None = None,
    stale_only: bool = True,
    dry_run: bool = False,
) -> PruneOutcome:
    """Delete generated copies whose recorded source disappeared.

    ``dry_run`` plans the same removals and reports them without writing.
    """
    config: BridgeConfig = load_config(config_path)
    profile = config.get_profile(profile_name)
    plan = plan_removals(
        profile.targets, stale_only=stale_only, skill_name=skill_name
    )
    removed = [] if dry_run else apply_removals(plan.items)
    return PruneOutcome(plan=plan, removed=removed, dry_run=dry_run)
