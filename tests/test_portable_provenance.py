"""Portable provenance: a committed copy must survive a clone or a new OS.

The marker still records the absolute source path, because that is the strongest
statement a copy can make and it is what older markers have. What it adds is the
source's path below the tree both lived in, so a copy checked out somewhere else
can find its source again instead of being reported — and pruned — as stale.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from skill_bridge import agent_paths
from skill_bridge.cli import main
from skill_bridge.config import BridgeConfig, SkillLocation, SyncProfile, save_config
from skill_bridge.doctor import STALE, diagnose
from skill_bridge.models import AgentFormat
from skill_bridge.provenance import read_marker, resolve_source, write_marker
from skill_bridge.sync import sync_profile

SKILL = (
    "---\n"
    "name: demo\n"
    "description: Demo skill for the provenance tests.\n"
    "---\n\n"
    "# Demo\n\n"
    "Body paragraph.\n"
)


def make_project(root: Path) -> SyncProfile:
    """A repo-shaped tree: a Claude source and a Hermes target beside it."""
    source = root / ".claude" / "skills" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return SyncProfile(
        name="test",
        source=SkillLocation(format=AgentFormat.GENERIC, path=source),
        targets=[
            SkillLocation(
                format=AgentFormat.HERMES, path=root / ".hermes" / "skills"
            )
        ],
    )


def hermes(root: Path) -> list[SkillLocation]:
    return [
        SkillLocation(format=AgentFormat.HERMES, path=root / ".hermes" / "skills")
    ]


def test_the_marker_records_where_the_source_sat_in_the_tree(tmp_path):
    root = tmp_path / "repo"
    sync_profile(make_project(root))

    marker = read_marker(root / ".hermes" / "skills" / "demo")

    assert marker["source-rel"] == ".claude/skills/demo"
    # The absolute path stays: it is the fastest and strongest check.
    assert Path(marker["source-path"]).is_absolute()


def test_a_clone_finds_its_source_again(tmp_path):
    origin = tmp_path / "origin" / "repo"
    sync_profile(make_project(origin))
    clone = tmp_path / "clone" / "repo"
    shutil.copytree(origin, clone)
    shutil.rmtree(origin)  # the checkout that wrote the marker is gone

    resolved = resolve_source(clone / ".hermes" / "skills" / "demo")

    assert resolved == (clone / ".claude" / "skills" / "demo").resolve()
    assert diagnose(hermes(clone)).clean


def test_a_clone_whose_source_is_really_gone_is_still_stale(tmp_path):
    origin = tmp_path / "origin" / "repo"
    sync_profile(make_project(origin))
    clone = tmp_path / "clone" / "repo"
    shutil.copytree(origin, clone)
    shutil.rmtree(origin)
    shutil.rmtree(clone / ".claude")

    report = diagnose(hermes(clone))

    assert [finding.kind for finding in report.findings] == [STALE]


def test_relocation_prefers_the_source_whose_fingerprint_matches(tmp_path):
    origin = tmp_path / "origin" / "repo"
    sync_profile(make_project(origin))
    clone = tmp_path / "clone" / "repo"
    shutil.copytree(origin, clone)
    shutil.rmtree(origin)
    # A nearer directory with the same relative path but different contents.
    decoy = clone / ".hermes" / "skills" / ".claude" / "skills" / "demo"
    decoy.mkdir(parents=True)
    (decoy / "SKILL.md").write_text(SKILL + "\nDecoy.\n", encoding="utf-8")

    resolved = resolve_source(clone / ".hermes" / "skills" / "demo")

    assert resolved == (clone / ".claude" / "skills" / "demo").resolve()


def test_a_marker_without_the_relative_path_is_unchanged(tmp_path):
    target = tmp_path / "target"
    copy = target / "demo"
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (copy / ".skill-bridge.json").write_text(
        json.dumps(
            {
                "generated-by": "skill-bridge",
                "version": "1.0.0",
                "target-format": "hermes",
                "source-format": "generic",
                "source-path": str(tmp_path / "gone" / "demo"),
            }
        ),
        encoding="utf-8",
    )

    # No relative path to fall back on: the recorded (missing) path is all the
    # marker can offer, and "the source is gone" is still the honest reading.
    assert resolve_source(copy) == tmp_path / "gone" / "demo"
    report = diagnose([SkillLocation(format=AgentFormat.HERMES, path=target)])
    assert [finding.kind for finding in report.findings] == [STALE]


def test_a_clone_checks_its_own_copies(tmp_path, monkeypatch):
    """The config, the marker and the copy all have to agree after a clone."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(agent_paths, "user_home", lambda: home)
    origin = tmp_path / "origin" / "repo"
    profile = make_project(origin)
    save_config(
        BridgeConfig(profiles={profile.name: profile}, default_profile=profile.name),
        origin / "skill-bridge.yaml",
    )
    sync_profile(profile)

    clone = tmp_path / "clone" / "repo"
    shutil.copytree(origin, clone)
    shutil.rmtree(origin)

    result = CliRunner().invoke(
        main,
        ["doctor", "--strict", "--json", "--config", str(clone / "skill-bridge.yaml")],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # The copy is checked, and it is not stale despite living somewhere new.
    assert payload["checked"] == 1
    assert payload["clean"] is True


def test_a_source_sharing_only_its_name_is_not_recorded(tmp_path):
    """One component says nothing about location, so it is not worth storing."""
    copy = tmp_path / "out" / "demo"
    copy.mkdir(parents=True)
    source = tmp_path / "demo"
    source.mkdir()

    write_marker(
        copy,
        source_format=AgentFormat.GENERIC,
        target_format=AgentFormat.HERMES,
        source_path=source,
    )

    assert "source-rel" not in read_marker(copy)
