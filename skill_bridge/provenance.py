"""Provenance markers for converted skill directories.

Every directory produced by skill-bridge carries a ``.skill-bridge.json`` marker.
The marker lets later syncs tell "I wrote this" apart from "the user authored
this here", so automatic imports can refresh their own output without ever
destroying a hand-written skill.
"""

from __future__ import annotations

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


def write_marker(
    skill_dir: Path,
    *,
    source_format: AgentFormat | None,
    target_format: AgentFormat,
    source_path: Path | None = None,
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
    path = marker_path(skill_dir)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
