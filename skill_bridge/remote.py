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
or credential helper, or fails with a message saying so instead of hanging. A
token written into the URL is lifted out of it when the source is parsed and
handed to git as a header scoped to that repository, so it appears neither in
a message that names the source nor in the checkout's own config.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

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


#: Schemes whose userinfo is a credential. `ssh://git@host/repo` names the ssh
#: user, which is part of where to connect rather than a secret to hide.
_CREDENTIAL_SCHEMES = frozenset({"http", "https"})


def _split_credentials(url: str) -> tuple[str, str | None, str | None]:
    """``url`` without its userinfo, and that userinfo as user and password.

    Git reads userinfo in an http(s) URL only to authenticate with it, but the
    text also lands in whatever names the repository and in the clone's own
    config. Taking it out here keeps it out of both: `_run` hands it back to
    git for one command, scoped to this origin.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url, None, None
    if parts.scheme.lower() not in _CREDENTIAL_SCHEMES or "@" not in parts.netloc:
        return url, None, None
    userinfo, _, host = parts.netloc.rpartition("@")
    if not userinfo or not host:
        return url, None, None
    username, sep, password = userinfo.partition(":")
    clean = urlunsplit(parts._replace(netloc=host))
    return clean, unquote(username), unquote(password) if sep else ""


@dataclass(frozen=True)
class GitSource:
    """A repository, the ref to read from it and the folder inside it."""

    url: str
    ref: str | None = None
    subdir: str | None = None
    #: Credentials lifted out of the URL. They are not part of the source's
    #: identity — two tokens to the same repository are the same checkout —
    #: and they stay out of its label, its repr and its cache key.
    username: str | None = field(default=None, repr=False, compare=False)
    password: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Hold the invariant on every construction path, not just `parse`."""
        clean, username, password = _split_credentials(self.url)
        if username is None and password is None:
            return
        object.__setattr__(self, "url", clean)
        if self.username is None:
            object.__setattr__(self, "username", username)
        if self.password is None:
            object.__setattr__(self, "password", password)

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
        clean, username, password = _split_credentials(text.strip())
        return cls(
            url=clean,
            ref=chosen or None,
            subdir=_subdir_path(subdir) if subdir else None,
            username=username,
            password=password,
        )

    @property
    def label(self) -> str:
        """How the source is named in reports: repository, ref, folder.

        The URL here never contains credentials, so this is safe to print — and
        every message, profile header and report goes through it.
        """
        text = f"{self.url}#{self.ref}" if self.ref else self.url
        return f"{text} ({self.subdir})" if self.subdir else text

    @property
    def config_url(self) -> str:
        """The URL as the profile wrote it, credentials included.

        Only `save_config` uses this: rewriting a profile must not turn a
        working private-repository source into one that cannot authenticate.
        Nothing displays it, and it is never handed to a command line.
        """
        if self.username is None and self.password is None:
            return self.url
        userinfo = quote(self.username or "", safe="")
        if self.password:
            # `user@host` and `user:@host` mean the same to git; the first is
            # what a token-only URL looked like before it was parsed.
            userinfo += ":" + quote(self.password, safe="")
        parts = urlsplit(self.url)
        return urlunsplit(parts._replace(netloc=f"{userinfo}@{parts.netloc}"))


def checkout_dir(git: GitSource) -> Path:
    """Where this repository is checked out, whether or not it exists yet."""
    digest = hashlib.sha256(git.url.encode("utf-8")).hexdigest()[:10]
    return cache_root() / GIT_DIR / f"{_slug(git.url)}-{digest}"


def _auth_env(git: GitSource) -> dict[str, str]:
    """Git config entries carrying ``git``'s credentials for one command.

    `git clone` copies a token in the URL into the checkout's own config, and
    git echoes the URL in some of its errors; a config entry set through the
    environment goes no further than the command it was set for. It is scoped
    to the repository's own origin, so a redirect or a submodule on another
    host never receives it, and it is not in argv, so `ps` does not show it.
    """
    if git.username is None and git.password is None:
        return {}
    secret = f"{git.username or ''}:{git.password or ''}".encode("utf-8")
    header = "AUTHORIZATION: basic " + base64.b64encode(secret).decode("ascii")
    parts = urlsplit(git.url)
    if parts.netloc:
        key = f"http.{parts.scheme}://{parts.netloc}/.extraheader"
    else:
        key = "http.extraheader"
    try:
        base = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        base = 0
    return {
        f"GIT_CONFIG_KEY_{base}": key,
        f"GIT_CONFIG_VALUE_{base}": header,
        "GIT_CONFIG_COUNT": str(base + 1),
    }


def _run(
    args: list[str], *, cwd: Path | None = None, auth: GitSource | None = None
) -> str:
    """Run one git command, turning every failure into a RemoteError."""
    if shutil.which("git") is None:
        raise RemoteError(
            "Reading a git source needs git on PATH, and it was not found. "
            "Install git, or point the profile at a local directory instead"
        )
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if auth is not None:
        env.update(_auth_env(auth))
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


def _rmtree(path: Path) -> None:
    """Remove a checkout, including the read-only files git leaves on Windows.

    `shutil.rmtree` alone stops at the first read-only object there, which is
    every file in `.git/objects`, so a checkout that has to be replaced would
    survive the attempt and fail the clone that follows.
    """
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chmod(Path(root) / name, 0o700)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


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
        _run(["clone", "--quiet", "--", git.url, str(staging)], auth=git)
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
            _rmtree(staging)


def _refresh(git: GitSource, dest: Path) -> None:
    """Move the checkout to what the remote has now."""
    _run(["fetch", "--quiet", "--tags", "--force", "origin"], cwd=dest, auth=git)
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
            _rmtree(dest)
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
