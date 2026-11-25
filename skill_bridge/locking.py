"""One mutating run at a time, per profile.

A conversion is atomic, but a sync is more than one conversion: it converts,
rewrites markers, prunes copies whose source vanished, and removes the retired
copies. Two runs doing that at once can interleave between those steps even
though no single file is ever torn — one prunes a copy the other is refreshing,
or both rewrite the same marker.

``sync``, ``prune`` and ``doctor --fix`` therefore take one advisory lock. It is
held by the operating system rather than by a marker file that has to be cleaned
up: a run killed outright releases it, and there is no stale lock to explain to
the next user.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

#: Created beside the config, so every run of that profile agrees on it.
LOCK_FILENAME = ".skill-bridge.lock"
#: Seconds a run waits for another one before giving up. A watch loop or a
#: second terminal usually wins by waiting; a hung run does not block forever.
DEFAULT_LOCK_WAIT = 10.0
_POLL = 0.05

#: Locks this process already holds, so nested calls do not deadlock on
#: themselves (``doctor --fix`` prunes and then syncs under one lock).
_held: set[str] = set()


class LockTimeout(RuntimeError):
    """Another run held the lock for longer than we were willing to wait."""


def lock_path(config_path: Path | None = None, *, base: Path | None = None) -> Path:
    """The lock guarding runs for the profile stored in ``config_path``.

    Without a config there is no profile to name, so the working directory — the
    project a run was started in — is the best shared anchor available.
    """
    if config_path is not None:
        directory = Path(config_path).resolve().parent
    else:
        directory = Path(base or Path.cwd()).resolve()
    return directory / LOCK_FILENAME


def _take(handle: IO[bytes]) -> None:
    if os.name == "nt":  # pragma: no cover - exercised by the Windows runners
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _give_back(handle: IO[bytes]) -> None:
    try:
        if os.name == "nt":  # pragma: no cover
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        handle.close()


def _holder(handle: IO[bytes]) -> str:
    """The pid the current holder wrote, when it managed to write one."""
    try:
        handle.seek(0)
        text = handle.read(64).decode("utf-8", "replace").strip()
    except OSError:
        return ""
    return f" (pid {text})" if text.isdigit() else ""


def _open_and_lock(path: Path, wait: float) -> IO[bytes]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - held for the length of the run
    deadline = time.monotonic() + max(wait, 0.0)
    while True:
        try:
            _take(handle)
        except OSError:
            if time.monotonic() >= deadline:
                holder = _holder(handle)
                handle.close()
                raise LockTimeout(
                    f"another skill-bridge run holds {path}{holder}; "
                    "wait for it to finish, or pass --lock-timeout to give up sooner"
                ) from None
            time.sleep(_POLL)
            continue
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n".encode())
            handle.flush()
        except OSError:
            pass  # the lock is what matters, not the note about it
        return handle


@contextmanager
def exclusive_run(path: Path, *, wait: float = DEFAULT_LOCK_WAIT) -> Iterator[None]:
    """Hold the lock at ``path`` for the duration of the block."""
    key = str(Path(path).resolve())
    if key in _held:
        yield  # a nested step of a run that already owns it
        return
    handle = _open_and_lock(Path(path), wait)
    _held.add(key)
    try:
        yield
    finally:
        _held.discard(key)
        _give_back(handle)
