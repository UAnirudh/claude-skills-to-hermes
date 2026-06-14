"""Live watch mode — re-sync skills when source files change."""

from __future__ import annotations

import time
from pathlib import Path

from skill_bridge.config import SyncProfile, load_config
from skill_bridge.sync import sync_profile


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
    """Debounce filesystem events and trigger sync."""

    def __init__(
        self,
        profile: SyncProfile,
        *,
        debounce_seconds: float = 1.0,
        on_sync=None,
    ) -> None:
        self.profile = profile
        self.debounce_seconds = debounce_seconds
        self.on_sync = on_sync
        self._last_run = 0.0
        self._pending = False

    def trigger(self, reason: str = "change") -> None:
        now = time.monotonic()
        if now - self._last_run < self.debounce_seconds:
            self._pending = True
            return
        self._last_run = now
        self._pending = False
        results = sync_profile(self.profile)
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
) -> None:
    """Block and watch source directory until interrupted."""
    FileSystemEventHandler, Observer = _try_watchdog()

    handler_obj = SkillWatchHandler(
        profile,
        debounce_seconds=debounce_seconds,
        on_sync=on_sync,
    )

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event) -> None:
            if event.is_directory:
                return
            src = Path(event.src_path)
            if src.name in {"SKILL.md", "skill.md"} or src.suffix in {".md", ".py", ".sh", ".yaml", ".yml"}:
                handler_obj.trigger(src.name)

    watch_path = str(profile.source.path)
    observer = Observer()
    observer.schedule(Handler(), watch_path, recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(0.5)
            handler_obj.flush_pending()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def watch_from_config(
    profile_name: str | None = None,
    config_path: Path | None = None,
    *,
    debounce_seconds: float = 1.0,
    on_sync=None,
) -> None:
    config = load_config(config_path)
    profile = config.get_profile(profile_name)
    if not profile.watch:
        raise RuntimeError(f"Profile '{profile.name}' has watch: false")
    watch_profile(profile, debounce_seconds=debounce_seconds, on_sync=on_sync)
