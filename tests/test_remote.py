"""Git sources: skills read from a repository instead of a harness directory.

A team that keeps its skills in one repository should not have to clone it by
hand and keep the clone somewhere a profile points at. These tests build real
repositories in temporary directories — no network — and check the checkout,
the refresh, the failure messages and the whole sync that reads them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from skill_bridge import cache
from skill_bridge.cache import cache_root
from skill_bridge.cli import main
from skill_bridge.config import (
    BridgeConfig,
    SkillLocation,
    SyncProfile,
    _dedupe_locations,
    load_config,
    save_config,
)
from skill_bridge.models import AgentFormat
from skill_bridge.remote import GitSource, RemoteError, checkout, checkout_dir
from skill_bridge.sync import sync_profile
from skill_bridge.watch import watch_roots

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")

SKILL = """---
name: code-review
description: Review a change before it lands.
---

# Review

{body}
"""


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def make_repo(root: Path, *, body: str = "Read the diff.") -> Path:
    """A repository with one skill at ``skills/code-review``."""
    repo = root / "skills-repo"
    skill = repo / "skills" / "code-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL.format(body=body), encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add code-review")
    return repo


def commit_change(repo: Path, body: str) -> None:
    (repo / "skills" / "code-review" / "SKILL.md").write_text(
        SKILL.format(body=body), encoding="utf-8"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "rewrite the skill")


def read_skill(path: Path) -> str:
    return (path / "code-review" / "SKILL.md").read_text(encoding="utf-8")


def write_profile(
    root: Path, *, sources: list[dict], targets: list[dict], name: str = "default"
) -> Path:
    path = root / "skill-bridge.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "default_profile": name,
                "profiles": {name: {"sources": sources, "targets": targets}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def git_source(url: str | Path, **kwargs) -> GitSource:
    return GitSource.parse(str(url), **kwargs)


# --- the source description ------------------------------------------------


def test_a_source_names_a_url_ref_and_folder():
    source = GitSource.parse(
        "https://github.com/acme/skills.git", ref="v2", subdir="skills/"
    )
    assert source.url == "https://github.com/acme/skills.git"
    assert source.ref == "v2"
    assert source.subdir == "skills"
    assert source.label == "https://github.com/acme/skills.git#v2 (skills)"


def test_a_hash_selects_the_ref():
    source = GitSource.parse("https://github.com/acme/skills#main")
    assert (source.url, source.ref) == ("https://github.com/acme/skills", "main")


def test_a_mapping_carries_the_ref_and_folder():
    source = GitSource.parse(
        {"git": "ssh://git@example.com/s.git", "ref": "dev", "path": "skills"}
    )
    assert source == GitSource("ssh://git@example.com/s.git", "dev", "skills")


def test_a_source_cannot_read_outside_its_checkout():
    with pytest.raises(RemoteError, match="inside the repository"):
        GitSource.parse("https://example.com/s.git", subdir="../elsewhere")


def test_a_source_needs_a_repository():
    with pytest.raises(RemoteError, match="needs a repository URL"):
        GitSource.parse("")


# --- checking a repository out ---------------------------------------------


def test_a_repository_is_checked_out_into_the_cache(tmp_path):
    repo = make_repo(tmp_path)
    source = git_source(repo, subdir="skills")

    path = checkout(source)

    assert path == checkout_dir(source) / "skills"
    assert path.is_dir()
    assert (path / "code-review" / "SKILL.md").is_file()


def test_a_folder_is_selected_inside_the_checkout(tmp_path):
    repo = make_repo(tmp_path)
    path = checkout(git_source(repo, subdir="skills/code-review"))
    assert (path / "SKILL.md").is_file()


def test_the_checkout_is_reused_until_it_is_refreshed(tmp_path):
    repo = make_repo(tmp_path)
    source = git_source(repo, subdir="skills")
    first = checkout(source)

    commit_change(repo, "Rewritten instructions.")
    again = checkout(source)

    assert again == first
    assert "Rewritten" not in read_skill(again)


def test_refreshing_moves_the_checkout_to_the_new_commit(tmp_path):
    repo = make_repo(tmp_path)
    source = git_source(repo, subdir="skills")
    checkout(source)

    commit_change(repo, "Rewritten instructions.")
    refreshed = checkout(source, refresh=True)

    assert "Rewritten instructions." in read_skill(refreshed)


def test_a_tag_pins_the_checkout(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "tag", "v1")
    commit_change(repo, "Only on main.")
    source = git_source(repo, ref="v1", subdir="skills")

    assert "Only on main." not in read_skill(checkout(source))
    # Refreshing re-reads the tag rather than following the branch.
    assert "Only on main." not in read_skill(checkout(source, refresh=True))


def test_a_branch_ref_follows_that_branch(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "next")
    commit_change(repo, "Written on next.")

    path = checkout(git_source(repo, ref="next", subdir="skills"))

    assert "Written on next." in read_skill(path)


def test_a_commit_ref_can_be_read(tmp_path):
    repo = make_repo(tmp_path)
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    commit_change(repo, "Later work on main.")

    path = checkout(git_source(repo, ref=first, subdir="skills"))

    assert "Later work" not in read_skill(path)


def test_a_folder_that_is_not_there_names_the_repository(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(RemoteError, match="no directory 'skills/nope'"):
        checkout(git_source(repo, subdir="skills/nope"))


def test_an_unknown_ref_is_reported(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(RemoteError, match="not a branch, tag or commit"):
        checkout(git_source(repo, ref="no-such-ref"))


def test_a_missing_repository_is_reported(tmp_path):
    with pytest.raises(RemoteError, match="git clone failed"):
        checkout(git_source(tmp_path / "not-a-repository"))


def test_git_missing_from_path_is_explained(monkeypatch):
    monkeypatch.setattr("skill_bridge.remote.shutil.which", lambda name: None)
    with pytest.raises(RemoteError, match="needs git on PATH"):
        checkout(git_source("https://example.invalid/skills.git"))


def test_an_unreachable_remote_keeps_the_last_checkout(tmp_path):
    repo = make_repo(tmp_path)
    source = git_source(repo, subdir="skills")
    first = checkout(source)
    # Moved rather than deleted: git leaves read-only files behind, and a
    # rename reproduces the same thing (a URL that no longer resolves) without
    # depending on the platform's permission rules.
    repo.rename(tmp_path / "moved-away")

    with pytest.warns(RuntimeWarning, match="Using the checkout"):
        path = checkout(source, refresh=True)

    assert path == first
    assert "Read the diff." in read_skill(path)


def test_a_half_written_checkout_is_replaced(tmp_path):
    repo = make_repo(tmp_path)
    source = git_source(repo, subdir="skills")
    dest = checkout_dir(source)
    dest.mkdir(parents=True)
    (dest / "leftover.txt").write_text("stale", encoding="utf-8")

    path = checkout(source)

    assert (path / "code-review" / "SKILL.md").is_file()
    assert not (dest / "leftover.txt").exists()


def test_a_checkout_is_never_evicted_with_the_conversion_cache(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    source = git_source(repo, subdir="skills")
    path = checkout(source)

    monkeypatch.setattr(cache, "MAX_BYTES", 0)
    monkeypatch.setattr(cache, "MAX_ENTRIES", 0)
    cache._evict(cache_root())

    assert (path / "code-review" / "SKILL.md").is_file()


# --- profiles --------------------------------------------------------------


def test_a_source_can_be_a_repository(tmp_path):
    loc = SkillLocation.from_dict(
        {
            "format": "claude-code",
            "git": "https://github.com/acme/skills.git",
            "ref": "main",
            "path": "skills",
        },
        project=tmp_path,
    )
    assert loc.git == GitSource("https://github.com/acme/skills.git", "main", "skills")
    assert loc.scope == "remote"
    assert loc.describe() == "claude-code:https://github.com/acme/skills.git#main (skills)"


def test_a_target_cannot_be_a_repository(tmp_path):
    path = write_profile(
        tmp_path,
        sources=[{"format": "claude-code", "git": "https://example.com/s.git"}],
        targets=[{"format": "hermes", "git": "https://example.com/t.git"}],
    )
    with pytest.raises(ValueError, match="only for sources"):
        load_config(path)


def test_two_repositories_are_not_deduped(tmp_path):
    one = SkillLocation(
        format=AgentFormat.CLAUDE_CODE,
        path=Path("."),
        scope="remote",
        git=GitSource("https://example.com/one.git"),
    )
    two = SkillLocation(
        format=AgentFormat.CLAUDE_CODE,
        path=Path("."),
        scope="remote",
        git=GitSource("https://example.com/two.git"),
    )
    assert _dedupe_locations([one, two]) == [one, two]


def test_a_repository_survives_a_config_round_trip(tmp_path):
    profile = SyncProfile(
        name="default",
        sources=[
            SkillLocation(
                format=AgentFormat.CLAUDE_CODE,
                path=Path("."),
                scope="remote",
                git=GitSource("https://example.com/s.git", "main", "skills"),
            )
        ],
        targets=[
            SkillLocation(format=AgentFormat.HERMES, path=tmp_path / "out", scope="custom")
        ],
    )
    path = save_config(
        BridgeConfig(profiles={"default": profile}, default_profile="default"),
        tmp_path / "skill-bridge.yaml",
    )

    loaded = load_config(path).profiles["default"].sources[0]

    assert loaded.git == GitSource("https://example.com/s.git", "main", "skills")
    assert "git: https://example.com/s.git" in path.read_text(encoding="utf-8")


# --- syncing from one ------------------------------------------------------


def test_sync_converts_the_skills_in_a_repository(tmp_path):
    repo = make_repo(tmp_path)
    target = tmp_path / "hermes"
    path = write_profile(
        tmp_path,
        sources=[{"format": "claude-code", "git": str(repo), "path": "skills"}],
        targets=[{"format": "hermes", "path": str(target)}],
    )

    results = sync_profile(load_config(path).get_profile(None))

    assert [result.skill_name for result in results] == ["code-review"]
    assert (target / "code-review" / "SKILL.md").is_file()


def test_sync_picks_up_a_change_pushed_to_the_repository(tmp_path):
    repo = make_repo(tmp_path)
    target = tmp_path / "hermes"
    path = write_profile(
        tmp_path,
        sources=[{"format": "claude-code", "git": str(repo), "path": "skills"}],
        targets=[{"format": "hermes", "path": str(target)}],
    )
    profile = load_config(path).get_profile(None)
    sync_profile(profile)

    commit_change(repo, "Rewritten instructions.")
    sync_profile(load_config(path).get_profile(None))

    assert "Rewritten instructions." in (target / "code-review" / "SKILL.md").read_text(
        encoding="utf-8"
    )


def test_watch_roots_include_the_checkout(tmp_path):
    repo = make_repo(tmp_path)
    profile = SyncProfile(
        name="default",
        source=SkillLocation.from_dict(
            {"format": "claude-code", "git": str(repo), "path": "skills"}
        ),
        targets=[SkillLocation(format=AgentFormat.HERMES, path=tmp_path / "out")],
    )

    roots = watch_roots(profile)

    assert roots == [checkout_dir(git_source(repo)) / "skills"]


# --- the CLI ---------------------------------------------------------------


def test_fetch_prints_where_the_checkout_landed(tmp_path):
    repo = make_repo(tmp_path)
    path = write_profile(
        tmp_path,
        sources=[{"format": "claude-code", "git": str(repo), "path": "skills"}],
        targets=[{"format": "hermes", "path": str(tmp_path / "out")}],
    )

    result = CliRunner().invoke(main, ["fetch", "-c", str(path)])

    assert result.exit_code == 0, result.output
    assert "1 git source(s) ready" in result.output
    assert (checkout_dir(git_source(repo)) / "skills" / "code-review").is_dir()


def test_fetch_says_when_a_profile_has_no_repositories(tmp_path):
    (tmp_path / "local").mkdir()
    path = write_profile(
        tmp_path,
        sources=[{"format": "claude-code", "path": str(tmp_path / "local")}],
        targets=[{"format": "hermes", "path": str(tmp_path / "out")}],
    )

    result = CliRunner().invoke(main, ["fetch", "-c", str(path)])

    assert result.exit_code == 0, result.output
    assert "has no git sources" in result.output


def test_fetch_reports_a_ref_that_does_not_resolve(tmp_path):
    repo = make_repo(tmp_path)
    path = write_profile(
        tmp_path,
        sources=[
            {"format": "claude-code", "git": str(repo), "ref": "nope", "path": "skills"}
        ],
        targets=[{"format": "hermes", "path": str(tmp_path / "out")}],
    )

    result = CliRunner().invoke(main, ["fetch", "-c", str(path)])

    assert result.exit_code == 1
    assert "not a branch, tag or commit" in result.output


def test_sync_reports_a_repository_it_cannot_read(tmp_path):
    path = write_profile(
        tmp_path,
        sources=[
            {"format": "claude-code", "git": str(tmp_path / "gone"), "path": "skills"}
        ],
        targets=[{"format": "hermes", "path": str(tmp_path / "out")}],
    )

    result = CliRunner().invoke(main, ["sync", "-c", str(path)])

    assert result.exit_code == 1
    assert "git clone failed" in result.output
