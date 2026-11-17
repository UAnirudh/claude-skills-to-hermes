"""Live watch mode — re-sync skills when any source harness changes."""

from __future__ import annotations

import os
import time
from pathlib import Path

from skill_bridge.config import SyncProfile, load_config
from skill_bridge.sync import profile_sources, sync_profile

#: How often to look for harness directories that appeared after startup.
RESCAN_SECONDS = 2.0
#: How often the loop checks for debounced work.
TICK_SECONDS = 0.5

WATCHED_SUFFIXES = {".md", ".py", ".sh", ".yaml", ".yml", ".json", ".toml"}

#: watchdog reports plain reads too. Reacting to those makes a sync trigger
#: itself, because syncing reads the source SKILL.md (infinite loop).
WRITE_EVENT_TYPES = {"created", "modified", "moved", "deleted"}


def _try_watchdog():
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        return FileSystemEventHandler, Observer
    except ImportError as exc:
        raise ImportError(
            "Watch mode requires watchdog. Install with: pip install skill-bridge[watch]"
        ) from exc


def is_relevant_event(event) -> bool:
    """True only for a write to a skill file we care about."""
    if getattr(event, "is_directory", False):
        return False
    if getattr(event, "event_type", "") not in WRITE_EVENT_TYPES:
        return False
    return Path(str(getattr(event, "src_path", ""))).suffix in WATCHED_SUFFIXES


def watch_roots(profile: SyncProfile) -> list[Path]:
    """Directories this profile should watch.

    Resolved from the profile itself, so profiles written by ``install``
    (``sources: auto``) keep working as harnesses are added or removed.
    Destination directories are excluded: several harnesses share
    ``.agents/skills``, and watching the output would make a sync trigger
    itself forever.
    """
    targets = {os.path.realpath(target.path) for target in profile.targets}
    roots: list[Path] = []
    seen: set[str] = set()

    for location in profile_sources(profile):
        if not location.path.is_dir():
            continue
        real = os.path.realpath(location.path)
        if real in targets or real in seen:
            continue
        seen.add(real)
        roots.append(location.path)
    return roots


class SkillWatchHandler:
    """Debounce filesystem events and trigger sync."""

    def __init__(
        self,
        profile: SyncProfile,
        *,
        debounce_seconds: float = 1.0,
        on_sync=None,
        on_error=None,
    ) -> None:
        self.profile = profile
        self.debounce_seconds = debounce_seconds
        self.on_sync = on_sync
        self.on_error = on_error
        self.last_error: Exception | None = None
        self._last_run = 0.0
        self._pending = False

    def trigger(self, reason: str = "change", *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_run < self.debounce_seconds:
            self._pending = True
            return
        self._last_run = now
        self._pending = False
        try:
            results = sync_profile(self.profile)
        except Exception as exc:  # noqa: BLE001 - keep the watcher alive
            self.last_error = exc
            if self.on_error:
                self.on_error(exc)
            return
        self.last_error = None
        if self.on_sync:
            self.on_sync(reason, results)

    def flush_pending(self) -> None:
        if self._pending:
            self.trigger("debounced")


def watch_profile(
    profile: SyncProfile,
    *,
    debounce_seconds: float = 1.0,
    on_sync=None,
    on_error=None,
) -> None:
    """Block and watch every source directory until interrupted."""
    if not profile.targets:
        raise RuntimeError(f"Profile '{profile.name}' has no targets to sync to")

    FileSystemEventHandler, Observer = _try_watchdog()

    handler = SkillWatchHandler(
        profile,
        debounce_seconds=debounce_seconds,
        on_sync=on_sync,
        on_error=on_error,
    )

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event) -> None:
            if is_relevant_event(event):
                handler.trigger(Path(str(event.src_path)).name)

    events = Handler()
    observer = Observer()
    scheduled: set[str] = set()

    def schedule_new_roots() -> bool:
        """Watch any source root seen for the first time. True if one was added."""
        added = False
        for root in watch_roots(profile):
            real = os.path.realpath(root)
            if real in scheduled:
                continue
            try:
                observer.schedule(events, str(root), recursive=True)
            except OSError:
                continue
            scheduled.add(real)
            added = True
        return added

    has_roots = schedule_new_roots()
    observer.start()

    # Convert anything that was already there (e.g. `watch` started after new
    # harnesses were installed) and never miss an event during that first pass.
    if has_roots:
        handler.trigger("startup", force=True)

    last_scan = time.monotonic()
    try:
        while True:
            time.sleep(TICK_SECONDS)
            handler.flush_pending()
            now = time.monotonic()
            if now - last_scan >= RESCAN_SECONDS:
                last_scan = now
                if schedule_new_roots():
                    # Skills may already exist in a harness installed mid-watch;
                    # there will be no filesystem event for them.
                    handler.trigger("new source harness", force=True)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def watch_from_config(
    profile_name: str | None = None,
    config_path: Path | None = None,
    *,
    debounce_seconds: float = 1.0,
    on_sync=None,
    on_error=None,
) -> None:
    config = load_config(config_path)
    profile = config.get_profile(profile_name)
    if not profile.watch:
        raise RuntimeError(f"Profile '{profile.name}' has watch: false")
    watch_profile(
        profile,
        debounce_seconds=debounce_seconds,
        on_sync=on_sync,
        on_error=on_error,
    )
