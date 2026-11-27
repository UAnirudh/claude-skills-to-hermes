"""On-disk cache of the conversions `doctor` and `diff` make only to compare.

Checking a copy that is behind means converting its source again and diffing
the result, which is the expensive half of a pass. That conversion is
deterministic: the same source, converted to the same target by the same code,
writes the same bytes. So the result is kept here, and the next check reads it
back instead of converting a second time — which is what makes a repeated
`doctor` (or `doctor` followed by `annotate` or `diff`) cheap.

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
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path

from skill_bridge.converter import convert_skill
from skill_bridge.models import AgentFormat
from skill_bridge.provenance import tree_files

#: Overrides where entries live. Tests point it at a temporary directory, and
#: anyone who wants the conversions out of their home can set it too.
CACHE_ENV = "SKILL_BRIDGE_CACHE_DIR"

#: Written last inside an entry, so its presence means the entry is complete.
MANIFEST = ".cache-key.json"

#: Checkouts of git skill sources live here, beside the entries and are never
#: evicted by the size bound: they are sources, not conversions to redo.
GIT_DIR = "git"

#: Bytes of stored conversions kept before the entries used longest ago are
#: dropped. The bound is a size rather than a count of entries because one
#: conversion is a few kilobytes: a profile with hundreds of copies fits, where
#: a count of 64 would evict the copies a pass had just converted and make the
#: next pass convert all of them again.
MAX_BYTES = 32 * 1024 * 1024

#: Ceiling on the number of entries whatever their size, so a cache of very
#: small conversions cannot fill the directory with entries.
MAX_ENTRIES = 2048

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
                    "bytes": sum(len(data) for data in files.values()),
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


def expected_files(
    source: Path, target: AgentFormat, source_hash: str
) -> tuple[dict[str, bytes] | None, str | None]:
    """What converting ``source`` to ``target`` writes, from the cache if kept.

    Exactly one of the pair is None: the files, or why they could not be worked
    out. Callers never convert themselves, so `doctor` and `diff` agree on what
    the conversion is and share the one entry kept for it.
    """
    cached = load(source_hash, target)
    if cached is not None:
        return cached, None
    scratch = Path(tempfile.mkdtemp(prefix="skill-bridge-convert-"))
    try:
        fresh = convert_skill(source, target, scratch, validate=False)
        expected = tree_files(fresh.output_dir)
    except (OSError, ValueError) as exc:
        return None, f"could not re-convert the source ({exc})"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if expected is None:
        return None, "conversion output cannot be read"
    store(source_hash, target, expected)
    return expected, None


def _entry_name(name: str) -> bool:
    """True for a directory name this module wrote as an entry key."""
    return len(name) == 64 and all(c in "0123456789abcdef" for c in name)


def _entries(root: Path) -> list[Path]:
    """Complete entries, ignoring temporary ones still being written.

    Only key-shaped directories count: the git checkouts kept beside the
    entries are sources, and dropping one to make room for a conversion would
    cost a clone rather than a conversion.
    """
    try:
        return [
            path
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith(".") and _entry_name(path.name)
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


def _tree_size(path: Path) -> int:
    """Bytes of the files in an entry, for entries that do not record it."""
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_dir():
                continue
            try:
                total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _size(path: Path) -> int:
    """Bytes of converted output an entry holds, as its manifest records."""
    manifest = _read_manifest(path)
    if manifest is not None:
        recorded = manifest.get("bytes")
        if isinstance(recorded, int) and recorded >= 0:
            return recorded
    return _tree_size(path)


def _evict(root: Path) -> None:
    """Keep the cache within its byte budget, dropping the oldest entries.

    The newest entry is always kept: it was just written by a check that would
    otherwise pay for that same conversion on its next run.
    """
    entries = _entries(root)
    if not entries:
        return
    sizes = {path: _size(path) for path in entries}
    total = sum(sizes.values())
    if len(entries) <= MAX_ENTRIES and total <= MAX_BYTES:
        return

    def used(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    oldest_first = sorted(entries, key=used)
    while len(oldest_first) > 1 and (
        len(oldest_first) > MAX_ENTRIES or total > MAX_BYTES
    ):
        path = oldest_first.pop(0)
        total -= sizes[path]
        shutil.rmtree(path, ignore_errors=True)
