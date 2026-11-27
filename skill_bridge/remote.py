"""Skills read from a git repository instead of a harness directory.

A team can keep one repository of skills and let every harness read from it:
a profile names the repository, skill-bridge keeps a checkout in the cache, and
`sync` converts from that copy exactly as it would from `~/.claude/skills`.

The checkout is a cache, not a second source of truth. `sync` fetches the
configured ref before it reads, so a `git push` reaches the harnesses; `watch`
keeps reading the checkout it already has instead of fetching on every file
event; and a fetch that fails falls back to the last checkout with a warning,
because an offline laptop can still sync what it last saw.

Nothing here guesses at credentials. Git is run with its terminal prompt
disabled, so a private repository either works through the user's own ssh key
or credential helper, or fails with a message saying so instead of hanging.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skill_bridge.cache import GIT_DIR, cache_root

#: Seconds a git command may take. A clone over a slow link is minutes; past
#: this the network has stopped answering rather than being slow.
GIT_TIMEOUT = 300


class RemoteError(RuntimeError):
    """A git source could not be read: missing repository, ref or git itself."""


def _slug(url: str) -> str:
    """A readable folder name for ``url``, so the cache can be inspected."""
    tail = url.rstrip("/").replace("\\", "/").rsplit("/", 1)[-1]
    tail = re.sub(r"\.git$", "", tail)
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-.")
    return cleaned or "repo"


def _subdir_path(raw: str) -> str | None:
    """Normalized repository-relative directory, or None for the whole repo."""
    text = str(raw).strip().replace("\\", "/").strip("/")
    if text in ("", "."):
        return None
    parts = Path(text).parts
    if Path(text).is_absolute() or ".." in parts:
        raise RemoteError(
            f"'{raw}' must be a directory inside the repository: "
            "a git source cannot read outside its own checkout"
        )
    return text


@dataclass(frozen=True)
class GitSource:
    """A repository, the ref to read from it and the folder inside it."""

    url: str
    ref: str | None = None
    subdir: str | None = None

    @classmethod
    def parse(
        cls,
        value: str | dict[str, Any] | None,
        *,
        ref: str | None = None,
        subdir: str | None = None,
    ) -> GitSource:
        """Read ``git:`` config, which is a URL, a ``#ref`` URL or a mapping."""
        if isinstance(value, dict):
            url = value.get("git") or value.get("url")
            ref = value.get("ref", ref)
            subdir = value.get("path", subdir) or value.get("subdir", subdir)
        else:
            url = value
        if url is None or not str(url).strip():
            raise RemoteError("A git source needs a repository URL in 'git:'")
        text = str(url).strip()
        # `https://host/repo#main` reads as "this repo at main". Git does not
        # use the fragment, so taking it here loses nothing.
        if "#" in text:
            text, _, inline = text.partition("#")
            ref = ref or inline
        if not text.strip():
            raise RemoteError(f"A git source needs a repository URL, not '{url}'")
        chosen = str(ref).strip() if ref else ""
        return cls(
            url=text.strip(),
            ref=chosen or None,
            subdir=_subdir_path(subdir) if subdir else None,
        )

    @property
    def label(self) -> str:
        """How the source is named in reports: repository, ref, folder."""
        text = f"{self.url}#{self.ref}" if self.ref else self.url
        return f"{text} ({self.subdir})" if self.subdir else text


def checkout_dir(git: GitSource) -> Path:
    """Where this repository is checked out, whether or not it exists yet."""
    digest = hashlib.sha256(git.url.encode("utf-8")).hexdigest()[:10]
    return cache_root() / GIT_DIR / f"{_slug(git.url)}-{digest}"


def _run(args: list[str], *, cwd: Path | None = None) -> str:
    """Run one git command, turning every failure into a RemoteError."""
    if shutil.which("git") is None:
        raise RemoteError(
            "Reading a git source needs git on PATH, and it was not found. "
            "Install git, or point the profile at a local directory instead"
        )
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteError(
            f"git {args[0]} did not finish within {GIT_TIMEOUT}s"
        ) from exc
    except OSError as exc:
        raise RemoteError(f"Could not run git {args[0]}: {exc}") from exc
    if proc.returncode == 0:
        return proc.stdout
    output = (proc.stderr or proc.stdout or "").strip()
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    detail = lines[-1] if lines else f"exit {proc.returncode}"
    if "terminal prompts disabled" in output:
        detail += (
            " (a private repository needs credentials git can use without a "
            "prompt: an ssh key, a credential helper or a token in the URL)"
        )
    raise RemoteError(f"git {args[0]} failed: {detail}")


def _has_commit(repo: Path, ref: str) -> bool:
    try:
        _run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo)
    except RemoteError:
        return False
    return True


def _select(repo: Path, ref: str) -> None:
    """Check ``ref`` out, preferring the fetched remote branch when there is one.

    After a fetch the remote-tracking branch has moved while a local branch of
    the same name has not, so `origin/main` is what the remote says now.
    """
    for candidate in (f"origin/{ref}", ref):
        if _has_commit(repo, candidate):
            _run(["checkout", "--quiet", "--force", candidate], cwd=repo)
            return
    raise RemoteError(
        f"'{ref}' is not a branch, tag or commit in {repo.parent.name}; "
        "check the ref in the profile, or fetch the repository by hand to see "
        "which refs it has"
    )


def _clone(git: GitSource, dest: Path) -> None:
    """Clone into a staging directory, then move it into place in one step."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}-", dir=dest.parent))
    try:
        _run(["clone", "--quiet", "--", git.url, str(staging)])
        if git.ref:
            _select(staging, git.ref)
        try:
            os.replace(staging, dest)
        except OSError:
            # Another run finished the same checkout first. Its copy is as good
            # as this one, so only a destination that is not a checkout at all
            # is an error worth reporting.
            if not (dest / ".git").is_dir():
                raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _refresh(git: GitSource, dest: Path) -> None:
    """Move the checkout to what the remote has now."""
    _run(["fetch", "--quiet", "--tags", "--force", "origin"], cwd=dest)
    if git.ref:
        _select(dest, git.ref)
    else:
        _run(["checkout", "--quiet", "--force", "FETCH_HEAD"], cwd=dest)


def _inside(git: GitSource, dest: Path) -> Path:
    if not git.subdir:
        return dest
    path = dest / git.subdir
    if not path.is_dir():
        raise RemoteError(
            f"{git.label} has no directory '{git.subdir}' "
            f"in the checkout at {dest}"
        )
    return path


def checkout(git: GitSource, *, refresh: bool = False) -> Path:
    """The directory to read skills from, cloning or updating as needed.

    ``refresh`` asks for the remote's current commit. Without it an existing
    checkout is read as it stands: a watch loop should not fetch on every file
    event, and the source is local to the machine either way. A refresh that
    fails keeps the checkout it already has and warns, so a run with no network
    still converts what it converted last time.
    """
    dest = checkout_dir(git)
    if not (dest / ".git").is_dir():
        if dest.exists():
            # A clone killed halfway leaves a directory with no repository in
            # it; nothing in there is worth keeping and clone would refuse it.
            shutil.rmtree(dest, ignore_errors=True)
        _clone(git, dest)
    elif refresh:
        try:
            _refresh(git, dest)
        except RemoteError as exc:
            warnings.warn(
                f"Could not refresh {git.label}: {exc}. "
                f"Using the checkout at {dest}.",
                RuntimeWarning,
                stacklevel=2,
            )
    return _inside(git, dest)
