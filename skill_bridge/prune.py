"""Removal of generated skill copies whose source is gone, or of an install.

Every conversion writes a ``.skill-bridge.json`` marker recording the source
directory. That recorded path is what makes deletion safe: a copy whose source
was deleted or renamed is provably ours and provably stale, while a skill the
harness authored has no marker and is never a candidate.

Two callers use this module:

* ``sync --prune`` deletes the copies whose recorded source disappeared;
* ``uninstall`` deletes every copy skill-bridge created in a harness.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from skill_bridge.config import SkillLocation
from skill_bridge.models import AgentFormat
from skill_bridge.parser import find_skills, is_skill_dir, load_skill
from skill_bridge.provenance import is_bridge_generated, read_marker
from skill_bridge.utils import slugify_name


@dataclass(frozen=True)
class RemovalItem:
    """One generated skill directory that may be deleted."""

    skill_dir: Path
    target_format: AgentFormat
    target_root: Path
    source_path: Path | None
    reason: str

    @property
    def skill_name(self) -> str:
        return self.skill_dir.name


@dataclass
class RemovalPlan:
    """What a prune/uninstall would do, plus what it deliberately left alone."""

    items: list[RemovalItem] = field(default_factory=list)
    kept_native: list[Path] = field(default_factory=list)

    def count(self) -> int:
        return len(self.items)


@dataclass
class PruneOutcome:
    plan: RemovalPlan
    removed: list[RemovalItem]
    dry_run: bool = False


def recorded_source(skill_dir: Path) -> Path | None:
    """The source directory the marker records, when it is safe to resolve."""
    raw = read_marker(skill_dir).get("source-path")
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    # Older or hand-edited markers can be relative; without an absolute path
    # "the source is gone" would just mean "relative to wherever sync ran".
    if not path.is_absolute():
        return None
    return path


def source_disappeared(skill_dir: Path) -> str | None:
    """Why a generated copy is stale, or ``None`` when it still matches.

    Only directories carrying the provenance marker are considered. A
    hand-written skill has no marker and can never be described as stale.
    """
    if not is_bridge_generated(skill_dir):
        return None
    source = recorded_source(skill_dir)
    if source is None:
        return None  # cannot prove where it came from -> leave it alone
    if not source.is_dir():
        return "source directory was deleted or renamed"
    if not is_skill_dir(source):
        return "source directory no longer holds a SKILL.md"
    try:
        name = load_skill(source).name
    except (OSError, ValueError):
        return "source skill is unreadable"
    if slugify_name(name) != slugify_name(skill_dir.name):
        return f"source skill was renamed to '{slugify_name(name)}'"
    return None


def plan_removals(
    locations: list[SkillLocation],
    *,
    stale_only: bool = True,
    skill_name: str | None = None,
) -> RemovalPlan:
    """Find generated copies to delete without touching the filesystem.

    ``stale_only`` keeps copies whose source still exists (``sync --prune``);
    otherwise every generated copy is a candidate (``uninstall``). Hand-written
    skills are collected in ``kept_native`` so callers can report them.
    """
    plan = RemovalPlan()
    wanted = slugify_name(skill_name) if skill_name else None
    seen_roots: set[str] = set()
    seen_dirs: set[str] = set()

    for loc in locations:
        root = Path(loc.path)
        if not root.is_dir():
            continue
        # Codex and Amp share ~/.agents/skills: scan each real folder once.
        root_key = os.path.realpath(root)
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)

        for skill_dir in find_skills(root):
            if not is_bridge_generated(skill_dir):
                plan.kept_native.append(skill_dir)
                continue
            if wanted and slugify_name(skill_dir.name) != wanted:
                continue
            real = os.path.realpath(skill_dir)
            if real in seen_dirs:
                continue
            seen_dirs.add(real)

            if stale_only:
                reason = source_disappeared(skill_dir)
                if reason is None:
                    continue
            else:
                reason = "created by skill-bridge"
            plan.items.append(
                RemovalItem(
                    skill_dir=skill_dir,
                    target_format=loc.format,
                    target_root=root,
                    source_path=recorded_source(skill_dir),
                    reason=reason,
                )
            )
    return plan


def apply_removals(items: list[RemovalItem]) -> list[RemovalItem]:
    """Delete the planned directories, re-checking ownership just before."""
    removed: list[RemovalItem] = []
    for item in items:
        root = Path(item.target_root).resolve()
        target = Path(item.skill_dir).resolve()
        # Only a direct child of the scanned folder, never the folder itself or
        # something reached through a symlink.
        if target.parent != root or Path(item.skill_dir).is_symlink():
            continue
        if not is_bridge_generated(item.skill_dir):
            continue  # marker gone since planning: no longer provably ours
        shutil.rmtree(item.skill_dir)
        removed.append(item)
    return removed
