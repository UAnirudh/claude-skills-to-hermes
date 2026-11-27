"""Live watch mode — re-sync skills when any source harness changes."""

from __future__ import annotations

import os
import threading
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


class _RootWatcher:
    """Keep the observer watching the profile's source roots as they change.

    A harness directory is routinely deleted and recreated — reinstalling a
    harness, switching branches, ``rm -rf ~/.claude`` — and the kernel drops
    the watch when that happens. Remembering only the paths that were
    scheduled once meant such a directory was never watched again, so a root
    that disappears is forgotten here and scheduled again when it comes back.
    """

    def __init__(self, observer, events, profile: SyncProfile) -> None:
        self.observer = observer
        self.events = events
        self.profile = profile
        #: Real path of each watched root, and the watch the observer returned.
        self.watches: dict[str, object] = {}

    def forget(self, path: str) -> None:
        """Drop the watch on a root that was deleted or moved under us.

        The kernel removes the watch with the directory, and no event will ever
        come from there again. Forgetting it here means the next rescan arms it
        again if the directory comes back before that rescan runs.
        """
        watch = self.watches.pop(os.path.realpath(path), None)
        if watch is None:
            return
        try:
            self.observer.unschedule(watch)
        except (KeyError, OSError):
            pass  # already gone: the kernel dropped it with the directory

    def refresh(self) -> bool:
        """Watch new roots, forget vanished ones. True if a root was added."""
        for real, watch in list(self.watches.items()):
            if Path(real).is_dir():
                continue
            try:
                self.observer.unschedule(watch)
            except (KeyError, OSError):
                pass  # already gone: the kernel dropped it with the directory
            del self.watches[real]

        added = False
        for root in watch_roots(self.profile):
            real = os.path.realpath(root)
            if real in self.watches:
                continue
            try:
                watch = self.observer.schedule(self.events, str(root), recursive=True)
            except OSError:
                continue  # vanished between listing and scheduling: next rescan
            self.watches[real] = watch
            added = True
        return added


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
        #: Held while a sync runs, so the watchdog thread and the main loop
        #: cannot start two of them at once.
        self._sync_lock = threading.Lock()

    def trigger(self, reason: str = "change", *, force: bool = False) -> None:
        """Sync once, unless one is already running or the debounce holds it.

        Called from the watchdog thread and from the main loop, so a sync that
        is already running is never started a second time: an event that
        arrives mid-sync is remembered and run once afterwards instead.
        """
        if not self._sync_lock.acquire(blocking=False):
            self._pending = True
            return
        try:
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
                self._report(self.on_error, exc)
                return
            self.last_error = None
            self._report(self.on_sync, reason, results)
        finally:
            self._sync_lock.release()

    def _report(self, callback, *args) -> None:
        """Call a user callback without letting it stop the watcher.

        `on_sync` usually prints, and printing to a closed pipe raises; that
        must end the one sync, not the watching.
        """
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:  # noqa: BLE001 - the watcher outlives it
            self.last_error = exc

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
            if getattr(event, "is_directory", False):
                # Deleting or moving a watched root takes its kernel watch with
                # it, and nothing from there is reported again. Forget it now,
                # so the next rescan re-arms it if the directory comes back.
                if getattr(event, "event_type", "") in {"deleted", "moved"}:
                    roots.forget(str(event.src_path))
                return
            if is_relevant_event(event):
                handler.trigger(Path(str(event.src_path)).name)

    events = Handler()
    observer = Observer()
    roots = _RootWatcher(observer, events, profile)
    started = False
    try:
        # Convert anything that was already there (e.g. `watch` started after
        # new harnesses were installed) and never miss an event during that
        # first pass.
        has_roots = roots.refresh()
        observer.start()
        started = True
        if has_roots:
            handler.trigger("startup", force=True)

        last_scan = time.monotonic()
        while True:
            time.sleep(TICK_SECONDS)
            if not observer.is_alive():
                # Pretending to watch is worse than stopping: the OS refused
                # or lost the watch (inotify limits, a root removed under it).
                raise RuntimeError(
                    "watch stopped: the file-system observer is no longer running"
                )
            handler.flush_pending()
            now = time.monotonic()
            if now - last_scan >= RESCAN_SECONDS:
                last_scan = now
                if roots.refresh():
                    # Skills may already exist in a harness installed mid-watch;
                    # there will be no filesystem event for them.
                    handler.trigger("new source harness", force=True)
    except KeyboardInterrupt:
        pass
    finally:
        # Always release the observer thread: leaving it behind after an error
        # keeps the process alive while nothing is being watched.
        observer.stop()
        if started:
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
