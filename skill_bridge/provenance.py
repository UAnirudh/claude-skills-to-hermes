"""Provenance markers for converted skill directories.

Every directory produced by skill-bridge carries a ``.skill-bridge.json`` marker.
The marker lets later syncs tell "I wrote this" apart from "the user authored
this here", so automatic imports can refresh their own output without ever
destroying a hand-written skill.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skill_bridge.models import AgentFormat

MARKER_FILENAME = ".skill-bridge.json"
GENERATOR = "skill-bridge"


def package_version() -> str:
    try:
        from importlib.metadata import version

        return version(GENERATOR)
    except Exception:  # noqa: BLE001 - running from a source checkout
        return "0.0.0"


def marker_path(skill_dir: Path) -> Path:
    return Path(skill_dir) / MARKER_FILENAME


def read_marker(skill_dir: Path) -> dict[str, Any]:
    path = marker_path(skill_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_bridge_generated(skill_dir: Path) -> bool:
    """True when skill-bridge created (and may therefore overwrite) this skill."""
    return read_marker(skill_dir).get("generated-by") == GENERATOR


def tree_hash(root: Path) -> str:
    """Stable fingerprint of a skill directory's files.

    Sorted relative paths and their bytes, so a renamed, added, removed or
    edited file changes the digest. The provenance marker is skipped: it holds
    a timestamp and these digests, so hashing it would make every copy differ
    from itself.
    """
    digest = hashlib.sha256()
    for entry in sorted(Path(root).rglob("*")):
        if entry.is_dir() or entry.name == MARKER_FILENAME:
            continue
        digest.update(entry.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _source_relative(skill_dir: Path, source_path: Path) -> str | None:
    """The source's path below the tree it shares with the copy, if it has one.

    Storing the absolute path alone makes a committed copy look stale the moment
    the repository is cloned somewhere else; this is the part that survives the
    move. A single component says nothing about where the source lives, so it is
    not worth recording.
    """
    try:
        copy_abs = os.path.abspath(skill_dir)
        source_abs = os.path.abspath(source_path)
        common = Path(os.path.commonpath([copy_abs, source_abs]))
        relative = Path(source_abs).relative_to(common)
    except ValueError:  # different drives on Windows, or no common ancestor
        return None
    if len(relative.parts) < 2:
        return None
    return relative.as_posix()


def _relocated(
    skill_dir: Path, relative: str, expected_source: str | None
) -> Path | None:
    """Find the source again by its relative path, walking up from the copy.

    A fingerprint match wins wherever it is found; otherwise the nearest
    candidate that exists is returned, because a source that changed since the
    copy was written can no longer match its own hash.
    """
    if not relative:
        return None
    parts = relative.split("/")
    if len(parts) < 2:
        return None
    closest: Path | None = None
    for ancestor in Path(skill_dir).resolve().parents:
        candidate = ancestor.joinpath(*parts)
        if not candidate.is_dir():
            continue
        if expected_source:
            try:
                if tree_hash(candidate) == expected_source:
                    return candidate
            except OSError:
                pass
        if closest is None:
            closest = candidate
    return closest


def resolve_source(
    skill_dir: Path, marker: dict[str, Any] | None = None
) -> Path | None:
    """The source directory this copy came from, if it can be found.

    The recorded absolute path is used when it still exists (the common case,
    and the only one older markers have). When it does not — the repository was
    cloned elsewhere, or the copy moved between operating systems — the source
    is looked up by its path relative to the tree both lived in.
    """
    data = read_marker(skill_dir) if marker is None else marker
    raw = data.get("source-path")
    if not raw:
        return None
    recorded = Path(str(raw)).expanduser()
    # A relative source path cannot prove anything: "gone" would only mean
    # "relative to wherever sync happened to run".
    if not recorded.is_absolute():
        return None
    if recorded.is_dir():
        return recorded
    # When it cannot be found anywhere, the recorded path is still the answer:
    # callers check that it exists and conclude the source is gone, which is
    # what a marker with no relative path could always prove.
    return _relocated(
        skill_dir, str(data.get("source-rel") or ""), data.get("source-sha")
    ) or recorded


def write_marker(
    skill_dir: Path,
    *,
    source_format: AgentFormat | None,
    target_format: AgentFormat,
    source_path: Path | None = None,
    source_hash: str | None = None,
    output_hash: str | None = None,
) -> Path:
    if not Path(skill_dir).is_dir():
        return marker_path(skill_dir)
    data: dict[str, Any] = {
        "generated-by": GENERATOR,
        "version": package_version(),
        "target-format": target_format.value,
        "source-format": (source_format or AgentFormat.GENERIC).value,
        "converted-at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if source_path is not None:
        data["source-path"] = str(source_path)
        relative = _source_relative(Path(skill_dir), Path(source_path))
        if relative is not None:
            data["source-rel"] = relative
    if source_hash is not None:
        data["source-sha"] = source_hash
    if output_hash is not None:
        data["output-sha"] = output_hash
    return _dump_marker(skill_dir, data)


def _dump_marker(skill_dir: Path, data: dict[str, Any]) -> Path:
    path = marker_path(skill_dir)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def bless_marker(skill_dir: Path, *, source: Path | None = None) -> Path:
    """Take this copy's current contents as the baseline for drift checks.

    ``accept`` calls this once the user has decided their hand edit is what they
    want: the copy and (when it can be read) its source are fingerprinted as
    they are now, which clears the warning, and ``local-changes`` records the
    decision so a later source change is reported as an edit rather than being
    refreshed over the top of it.
    """
    if not is_bridge_generated(skill_dir):
        return marker_path(skill_dir)
    data = read_marker(skill_dir)
    data["output-sha"] = tree_hash(skill_dir)
    if source is not None and Path(source).is_dir():
        try:
            data["source-sha"] = tree_hash(source)
        except OSError:
            pass  # the copy can still be blessed; the source is just unreadable
    data["accepted-at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["local-changes"] = True
    return _dump_marker(skill_dir, data)
