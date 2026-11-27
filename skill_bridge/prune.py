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
from skill_bridge.provenance import is_bridge_generated, read_marker, resolve_source
from skill_bridge.remote import GitSource, RemoteError, checkout_dir
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
    #: Copies whose git source is simply not checked out yet: not removals,
    #: because the next fetch brings their source back.
    pending: list[Path] = field(default_factory=list)

    def count(self) -> int:
        return len(self.items)


@dataclass
class PruneOutcome:
    plan: RemovalPlan
    removed: list[RemovalItem]
    dry_run: bool = False


def recorded_source(skill_dir: Path) -> Path | None:
    """The source directory the marker records, when it is safe to resolve.

    Markers also store where the source sat relative to the tree the copy lives
    in, so a copy committed to a repository still resolves after a clone: the
    absolute path is gone, but the tree it named is not.
    """
    return resolve_source(skill_dir)


def git_origin(skill_dir: Path) -> tuple[GitSource, str] | None:
    """The repository a copy was read from, and its path inside the checkout.

    Recorded in the marker when the copy was converted from a `git:` source.
    A marker from before that existed simply has no origin, and everything
    below keeps its old meaning for it.
    """
    data = read_marker(skill_dir).get("source-git")
    if not isinstance(data, dict):
        return None
    url = str(data.get("url") or "").strip()
    if not url:
        return None
    ref = data.get("ref")
    try:
        git = GitSource(url=url, ref=str(ref) if ref else None)
    except RemoteError:
        return None
    return git, str(data.get("path") or "")


def pending_fetch(skill_dir: Path) -> Path | None:
    """Where a git source would be checked out, when it is not there yet.

    A missing checkout is not a missing source: `skill-bridge fetch` — or the
    next `sync` — recreates it at this same path, because the path is derived
    from the repository and ref alone. Callers that would delete the copy or
    convert it onward have to wait instead of acting on it.
    """
    origin = git_origin(skill_dir)
    if origin is None:
        return None
    dest = checkout_dir(origin[0])
    return None if (dest / ".git").is_dir() else dest


def source_disappeared(skill_dir: Path) -> str | None:
    """Why a generated copy is stale, or ``None`` when it still matches.

    Only directories carrying the provenance marker are considered. A
    hand-written skill has no marker and can never be described as stale, and
    neither can a copy whose repository has simply not been fetched yet.
    """
    if not is_bridge_generated(skill_dir):
        return None
    if pending_fetch(skill_dir) is not None:
        return None  # not fetched yet: nothing here proves the source is gone
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
                waiting = pending_fetch(skill_dir)
                if waiting is not None:
                    plan.pending.append(skill_dir)
                    continue
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
