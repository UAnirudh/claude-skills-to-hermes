"""Live watch mode — re-sync skills when source files change."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from skill_bridge.config import SyncProfile, load_config
from skill_bridge.sync import sync_profile

# Directories that never hold authored skills worth re-syncing.
IGNORED_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv"}


def _try_watchdog():
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        return FileSystemEventHandler, Observer
    except ImportError as exc:
        raise ImportError(
            "Watch mode requires watchdog. Install with: pip install skill-bridge[watch]"
        ) from exc


class SkillWatchHandler:
    """Debounce filesystem events and run one sync per settled burst."""

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
        self.last_event = 0.0
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = "change"

    def trigger(self, reason: str = "change") -> None:
        """Record a change; the sync itself runs from the watch loop."""
        with self._lock:
            self._reason = reason
            self.last_event = time.monotonic()
        self._event.set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)

    def run(self, reason: str | None = None) -> list:
        """Sync the profile now and report the outcome."""
        with self._lock:
            self._event.clear()
            reason = reason or self._reason
        try:
            # A watched source may legitimately hold no skills yet; that is
            # not a failure, just nothing to do until files appear.
            results = sync_profile(self.profile, allow_empty=True)
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            if self.on_error is None:
                raise
            self.on_error(reason, exc)
            return []
        if self.on_sync:
            self.on_sync(reason, results)
        return results


def watch_profile(
    profile: SyncProfile,
    *,
    debounce_seconds: float = 1.0,
    on_sync=None,
    on_error=None,
    initial_sync: bool = True,
) -> None:
    """Block and watch the source directory until interrupted.

    The profile is synced once at startup so skills that already exist are
    converted, then again after each change burst.
    """
    FileSystemEventHandler, Observer = _try_watchdog()

    source = profile.source.path.resolve()
    if not source.is_dir():
        raise FileNotFoundError(
            f"Source path missing for profile '{profile.name}': {source}"
        )

    # Only targets inside the watched tree can echo events back at us.
    target_roots: list[Path] = []
    for target in profile.targets:
        resolved = target.path.resolve()
        if resolved != source and source in resolved.parents:
            target_roots.append(resolved)

    handler_obj = SkillWatchHandler(
        profile,
        debounce_seconds=debounce_seconds,
        on_sync=on_sync,
        on_error=on_error,
    )

    def is_ignored(path: Path) -> bool:
        if any(part in IGNORED_DIRS for part in path.parts):
            return True
        return any(path == root or root in path.parents for root in target_roots)

    if initial_sync:
        handler_obj.run("initial")

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event) -> None:
            if event.is_directory:
                return
            if getattr(event, "event_type", "") in {"opened", "closed_no_write"}:
                return
            for raw in (getattr(event, "src_path", ""), getattr(event, "dest_path", "")):
                if not raw:
                    continue
                path = Path(raw)
                if is_ignored(path):
                    continue
                # Any change can alter output: supporting files are copied as-is.
                handler_obj.trigger(path.name)
                return

    observer = Observer()
    observer.schedule(Handler(), str(source), recursive=True)
    observer.start()

    try:
        while True:
            if not handler_obj.wait(0.25):
                continue
            remaining = handler_obj.debounce_seconds - (
                time.monotonic() - handler_obj.last_event
            )
            if remaining > 0:
                # Let the rest of the burst arrive before syncing once.
                time.sleep(remaining)
                continue
            handler_obj.run()
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


def watch_from_config(
    profile_name: str | None = None,
    config_path: Path | None = None,
    *,
    debounce_seconds: float = 1.0,
    on_sync=None,
    on_error=None,
    initial_sync: bool = True,
    force: bool = False,
) -> None:
    """Watch a configured profile.

    Profiles configured with ``watch: false`` (as reverse-sync profiles
    commonly are) are refused unless ``force`` is set; the ``watch`` CLI
    command sets it because an explicit request should win.
    """
    config = load_config(config_path)
    profile = config.get_profile(profile_name)
    if not profile.watch and not force:
        raise RuntimeError(
            f"Profile '{profile.name}' has watch: false. "
            "Pass force=True to watch it anyway."
        )
    watch_profile(
        profile,
        debounce_seconds=debounce_seconds,
        on_sync=on_sync,
        on_error=on_error,
        initial_sync=initial_sync,
    )
