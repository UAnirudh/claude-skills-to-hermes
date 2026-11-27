"""On-disk cache of the conversions `doctor` makes only to compare against.

Checking a copy that is behind means converting its source again and diffing
the result, which is the expensive half of a pass. That conversion is
deterministic: the same source, converted to the same target by the same code,
writes the same bytes. So the result is kept here, and the next check reads it
back instead of converting a second time — which is what makes a repeated
`doctor` (or `doctor` followed by `annotate`) cheap.

The key is the source's content, the target format and a digest of the
converter's own code. The last one is why a converter fix invalidates the
cache even in a source checkout whose installed metadata still names the old
version: what matters is the code that would produce the output, not the
number it reports.

Nothing here is authoritative. A missing, unreadable or half-written entry is
a miss, the caller converts exactly as it would have without a cache, and the
report is identical either way. Entries are written into a temporary directory
and renamed into place, so a reader only ever sees a complete one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from functools import lru_cache
from pathlib import Path

from skill_bridge.models import AgentFormat

#: Overrides where entries live. Tests point it at a temporary directory, and
#: anyone who wants the conversions out of their home can set it too.
CACHE_ENV = "SKILL_BRIDGE_CACHE_DIR"

#: Written last inside an entry, so its presence means the entry is complete.
MANIFEST = ".cache-key.json"

#: Entries kept before the oldest are dropped. A profile is checked far more
#: often than its sources change, so a small bound still covers the common case.
MAX_ENTRIES = 64

_TMP_PREFIX = ".tmp-"
_STALE_TMP_SECONDS = 3600


def cache_root() -> Path:
    """The directory entries live in, following the platform's convention."""
    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "skill-bridge" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "skill-bridge"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "skill-bridge"


@lru_cache(maxsize=1)
def code_digest() -> str:
    """Fingerprint of the code that produces a conversion, or "" if unreadable.

    Every module in the package is hashed, which includes the version the
    package reports: bumping it changes this, and so does a converter fix that
    was never released. An empty digest disables the cache rather than mixing
    outputs from code that could not be identified.
    """
    root = Path(__file__).parent
    digest = hashlib.sha256()
    try:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except OSError:
        return ""
    return digest.hexdigest()


def key_for(source_hash: str, target: AgentFormat) -> str | None:
    """Cache key for converting ``source_hash`` to ``target``, or None."""
    code = code_digest()
    if not code or not source_hash:
        return None
    material = json.dumps(
        {"source": source_hash, "target": target.value, "code": code},
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _read_manifest(entry: Path) -> dict[str, object] | None:
    try:
        data = json.loads((entry / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load(source_hash: str, target: AgentFormat) -> dict[str, bytes] | None:
    """The files a conversion of this source produced, or None on a miss."""
    key = key_for(source_hash, target)
    if key is None:
        return None
    entry = cache_root() / key
    manifest = _read_manifest(entry)
    if manifest is None or manifest.get("key") != key:
        return None
    files: dict[str, bytes] = {}
    try:
        for path in sorted(entry.rglob("*")):
            if path.is_dir():
                continue
            name = path.relative_to(entry).as_posix()
            if name == MANIFEST:
                continue
            files[name] = path.read_bytes()
    except OSError:
        return None
    # Reading is a use: keep recently checked entries when the bound is hit.
    try:
        os.utime(entry, None)
    except OSError:
        pass
    return files


def store(source_hash: str, target: AgentFormat, files: dict[str, bytes]) -> None:
    """Keep ``files`` as what converting this source to this target produces."""
    key = key_for(source_hash, target)
    if key is None:
        return
    root = cache_root()
    entry = root / key
    if entry.is_dir():
        manifest = _read_manifest(entry)
        if manifest is not None and manifest.get("key") == key:
            return  # already stored, and a complete entry never changes
        # A damaged entry: replace it, rather than leave a miss that every
        # later check would pay for again.
        shutil.rmtree(entry, ignore_errors=True)
    tmp = root / f"{_TMP_PREFIX}{uuid.uuid4().hex}"
    try:
        tmp.mkdir(parents=True)
        for name, data in files.items():
            path = tmp / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (tmp / MANIFEST).write_text(
            json.dumps(
                {
                    "key": key,
                    "source": source_hash,
                    "target": target.value,
                    "code": code_digest(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.rename(tmp, entry)
    except OSError:
        # A concurrent run stored the same entry, or the cache is unwritable.
        # Either way the check is still correct, it just converts next time.
        shutil.rmtree(tmp, ignore_errors=True)
        return
    _sweep(root)
    _evict(root)


def _entries(root: Path) -> list[Path]:
    """Complete entries, ignoring temporary ones still being written."""
    try:
        return [
            path
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ]
    except OSError:
        return []


def _sweep(root: Path) -> None:
    """Drop temporary directories a killed run left behind an hour ago."""
    cutoff = time.time() - _STALE_TMP_SECONDS
    try:
        candidates = list(root.iterdir())
    except OSError:
        return
    for path in candidates:
        if not path.name.startswith(_TMP_PREFIX):
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)


def _evict(root: Path) -> None:
    """Keep the cache bounded, dropping the entries used longest ago."""
    entries = _entries(root)
    if len(entries) <= MAX_ENTRIES:
        return

    def used(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    for path in sorted(entries, key=used)[: len(entries) - MAX_ENTRIES]:
        shutil.rmtree(path, ignore_errors=True)
