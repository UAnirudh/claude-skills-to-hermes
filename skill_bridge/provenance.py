"""Provenance markers for converted skill directories.

Every directory produced by skill-bridge carries a ``.skill-bridge.json`` marker.
The marker lets later syncs tell "I wrote this" apart from "the user authored
this here", so automatic imports can refresh their own output without ever
destroying a hand-written skill.
"""

from __future__ import annotations

import hashlib
import json
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
    if source_hash is not None:
        data["source-sha"] = source_hash
    if output_hash is not None:
        data["output-sha"] = output_hash
    path = marker_path(skill_dir)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
