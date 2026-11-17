"""Import skills from every other harness on the machine into one harness.

This is the "install it and it just works" path: point it at the harness you
use and it finds the skill directories of every other agent on the machine,
converts each skill into your harness's layout, and records provenance so the
next run refreshes its own output instead of duplicating it.

Safety rules:

* a skill that your harness authored itself is never overwritten without
  ``force``;
* a source directory that is also the destination directory is skipped, so
  harnesses sharing ``.agents/skills`` cannot recursively copy into themselves;
* duplicate skill names across harnesses are imported once, first one wins.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from skill_bridge.agent_paths import resolve_skill_path
from skill_bridge.converter import convert_skill
from skill_bridge.formats.base import load_exporters
from skill_bridge.models import AgentFormat, ConversionResult
from skill_bridge.parser import find_skills, load_skill
from skill_bridge.provenance import is_bridge_generated, read_marker
from skill_bridge.utils import slugify_name

ACTION_IMPORT = "import"
ACTION_UPDATE = "update"
ACTION_OVERWRITE = "overwrite"
ACTION_SKIP = "skip"
ACTION_ERROR = "error"

WRITE_ACTIONS = {ACTION_IMPORT, ACTION_UPDATE, ACTION_OVERWRITE}


def candidate_source_formats(
    target: AgentFormat,
    from_formats: list[AgentFormat] | None = None,
) -> list[AgentFormat]:
    if from_formats:
        return [fmt for fmt in from_formats if fmt is not target]
    return [fmt for fmt in AgentFormat if fmt not in (target, AgentFormat.GENERIC)]


def source_roots(
    target: AgentFormat,
    *,
    from_formats: list[AgentFormat] | None = None,
    scope: str = "global",
    project: Path | None = None,
    target_path: Path | None = None,
) -> list[tuple[AgentFormat, Path]]:
    """Skill roots of every other harness, de-duplicated by real path."""
    scopes = ["global", "project"] if scope == "both" else [scope]
    roots: list[tuple[AgentFormat, Path]] = []
    seen: set[str] = set()

    target_root = resolve_skill_path(
        target,
        scope="project" if scope == "project" else "global",
        project=project,
        override=target_path,
    )
    target_real = os.path.realpath(target_root)

    for fmt in candidate_source_formats(target, from_formats):
        for one_scope in scopes:
            path = resolve_skill_path(fmt, scope=one_scope, project=project)
            if not path.is_dir():
                continue
            key = os.path.realpath(path)
            # Harnesses can share a folder (.agents/skills). Never treat the
            # destination itself as a source, or a copy would chase its tail.
            if key in seen or key == target_real:
                continue
            seen.add(key)
            roots.append((fmt, path))
    return roots


@dataclass
class AdoptItem:
    """One skill seen in another harness and what will happen to it."""

    skill_name: str
    source_format: AgentFormat
    source_dir: Path
    destination: Path
    action: str
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class AdoptReport:
    target_format: AgentFormat
    target_root: Path
    items: list[AdoptItem] = field(default_factory=list)
    results: list[ConversionResult] = field(default_factory=list)

    def selected(self) -> list[AdoptItem]:
        return [item for item in self.items if item.action in WRITE_ACTIONS]

    def count(self, action: str) -> int:
        return sum(1 for item in self.items if item.action == action)

    def skill_names(self, *, written_only: bool = False) -> list[str]:
        source = self.selected() if written_only else self.items
        return [item.skill_name for item in source]


def plan_adopt(
    target_format: AgentFormat,
    *,
    from_formats: list[AgentFormat] | None = None,
    scope: str = "global",
    project: Path | None = None,
    target_path: Path | None = None,
    force: bool = False,
) -> AdoptReport:
    """Work out what would be imported, without touching the filesystem."""
    load_exporters()

    target_root = resolve_skill_path(
        target_format,
        scope="project" if scope == "project" else "global",
        project=project,
        override=target_path,
    )
    report = AdoptReport(target_format=target_format, target_root=target_root)

    target_real = os.path.realpath(target_root)
    taken: dict[str, Path] = {}
    seen_paths: set[str] = set()

    for fmt, root in source_roots(
        target_format,
        from_formats=from_formats,
        scope=scope,
        project=project,
        target_path=target_path,
    ):
        for skill_dir in find_skills(root):
            real = os.path.realpath(skill_dir)
            if real in seen_paths:
                continue
            seen_paths.add(real)
            if real == target_real or real.startswith(target_real + os.sep):
                continue

            try:
                bundle = load_skill(skill_dir)
            except (OSError, ValueError) as exc:
                report.items.append(
                    AdoptItem(
                        skill_name=skill_dir.name,
                        source_format=fmt,
                        source_dir=skill_dir,
                        destination=target_root / skill_dir.name,
                        action=ACTION_ERROR,
                        reason=f"unreadable skill: {exc}",
                    )
                )
                continue

            name = slugify_name(bundle.name or skill_dir.name)
            destination = target_root / name

            if name in taken:
                report.items.append(
                    AdoptItem(
                        skill_name=name,
                        source_format=fmt,
                        source_dir=skill_dir,
                        destination=destination,
                        action=ACTION_SKIP,
                        reason=f"duplicate name; already importing from {taken[name]}",
                    )
                )
                continue
            taken[name] = skill_dir

            if not destination.exists():
                action, reason = ACTION_IMPORT, ""
            elif force:
                action, reason = ACTION_OVERWRITE, "forced overwrite"
            elif is_bridge_generated(destination):
                marker = read_marker(destination)
                source_hint = marker.get("source-format", "unknown")
                action = ACTION_UPDATE
                reason = f"refresh of a skill converted earlier (from {source_hint})"
            else:
                action = ACTION_SKIP
                reason = "exists and was not created by skill-bridge (use --force)"

            report.items.append(
                AdoptItem(
                    skill_name=name,
                    source_format=fmt,
                    source_dir=skill_dir,
                    destination=destination,
                    action=action,
                    reason=reason,
                )
            )

    return report


def run_adopt(
    target_format: AgentFormat,
    *,
    from_formats: list[AgentFormat] | None = None,
    scope: str = "global",
    project: Path | None = None,
    target_path: Path | None = None,
    force: bool = False,
    validate: bool = True,
    dry_run: bool = False,
) -> AdoptReport:
    """Convert every other harness's skills into ``target_format``."""
    report = plan_adopt(
        target_format,
        from_formats=from_formats,
        scope=scope,
        project=project,
        target_path=target_path,
        force=force,
    )
    if dry_run:
        return report

    for item in report.selected():
        result = convert_skill(
            item.source_dir,
            target_format,
            report.target_root,
            validate=validate,
        )
        item.warnings.extend(result.warnings)
        report.results.append(result)
    return report
