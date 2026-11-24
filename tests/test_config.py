"""Profile composition: the ``auto-import`` profile and re-runs of ``install``."""

from pathlib import Path

import pytest

from skill_bridge import agent_paths
from skill_bridge.agent_paths import global_skill_root
from skill_bridge.config import (
    BridgeConfig,
    SkillLocation,
    SyncProfile,
    auto_import_config,
    load_config,
    save_config,
    update_auto_import_config,
)
from skill_bridge.models import AgentFormat


@pytest.fixture
def project(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(agent_paths, "user_home", lambda: fake_home)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    return repo


def test_global_profile_targets_the_global_copy(project):
    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)

    assert profile.name == "auto-import"
    assert profile.auto_sources is True
    assert profile.source_scope == "global"
    assert [t.path for t in profile.targets] == [global_skill_root(AgentFormat.HERMES)]
    assert profile.targets[0].scope == "global"


def test_shared_skill_directories_become_one_target(project):
    # Codex and Amp both read ~/.agents/skills.
    profile = auto_import_config([AgentFormat.CODEX, AgentFormat.AMP]).get_profile(None)

    assert [t.format for t in profile.targets] == [AgentFormat.CODEX]


def test_project_scope_points_at_the_project_copy(project):
    profile = auto_import_config(AgentFormat.HERMES, scope="project").get_profile(None)

    assert profile.source_scope == "project"
    assert [t.path for t in profile.targets] == [project / ".hermes" / "skills"]
    assert profile.targets[0].scope == "project"


def test_both_scope_covers_the_global_and_project_copies(project):
    profile = auto_import_config(AgentFormat.HERMES, scope="both").get_profile(None)

    assert profile.source_scope == "both"
    assert [t.path for t in profile.targets] == [
        global_skill_root(AgentFormat.HERMES),
        project / ".hermes" / "skills",
    ]


def test_explicit_sources_follow_the_scope(project):
    profile = auto_import_config(
        AgentFormat.HERMES,
        from_formats=[AgentFormat.CURSOR],
        scope="both",
    ).get_profile(None)

    assert profile.auto_sources is False
    assert [s.path for s in profile.sources] == [
        global_skill_root(AgentFormat.CURSOR),
        project / ".cursor" / "skills",
    ]


def test_a_target_is_never_also_a_source(project):
    profile = auto_import_config(
        AgentFormat.HERMES,
        from_formats=[AgentFormat.HERMES, AgentFormat.CURSOR],
    ).get_profile(None)

    assert [s.format for s in profile.sources] == [AgentFormat.CURSOR]


def test_auto_import_needs_a_target():
    with pytest.raises(ValueError, match="at least one target"):
        auto_import_config([])


def test_target_path_only_makes_sense_for_one_target(tmp_path):
    with pytest.raises(ValueError, match="single target"):
        auto_import_config(
            [AgentFormat.HERMES, AgentFormat.CURSOR],
            target_path=tmp_path / "custom",
        )


def test_update_keeps_targets_for_harnesses_not_seen_this_run(project):
    existing = auto_import_config(AgentFormat.HERMES)

    updated = update_auto_import_config(existing, [AgentFormat.CURSOR])
    targets = updated.get_profile(None).targets

    assert [t.format for t in targets] == [AgentFormat.HERMES, AgentFormat.CURSOR]


def test_replace_targets_moves_the_profile_to_the_new_scope(project):
    existing = auto_import_config(AgentFormat.HERMES)

    updated = update_auto_import_config(
        existing,
        [AgentFormat.HERMES],
        scope="project",
        replace_targets=True,
    )
    profile = updated.get_profile(None)

    assert [t.path for t in profile.targets] == [project / ".hermes" / "skills"]
    assert profile.source_scope == "project"


def test_update_preserves_hand_edited_sources_and_other_profiles(project):
    existing = auto_import_config(AgentFormat.HERMES)
    edited = existing.profiles["auto-import"]
    edited.auto_sources = False
    edited.sources = [
        SkillLocation(
            format=AgentFormat.GEMINI,
            path=global_skill_root(AgentFormat.GEMINI),
            scope="global",
        )
    ]
    existing.profiles["mine"] = SyncProfile(
        name="mine",
        auto_sources=True,
        targets=[SkillLocation(format=AgentFormat.CURSOR, path=project / "custom")],
    )
    existing.default_profile = "mine"

    updated = update_auto_import_config(existing, [AgentFormat.CURSOR])
    auto_import = updated.get_profile("auto-import")

    assert [s.format for s in auto_import.sources] == [AgentFormat.GEMINI]
    assert auto_import.auto_sources is False
    assert "mine" in updated.profiles
    assert updated.default_profile == "mine"


def test_both_scope_round_trips_through_the_config_file(project):
    path = save_config(
        auto_import_config(AgentFormat.HERMES, scope="both"),
        project / "skill-bridge.yaml",
    )

    profile = load_config(path).get_profile(None)

    assert profile.source_scope == "both"
    assert [t.path for t in profile.targets] == [
        global_skill_root(AgentFormat.HERMES),
        project / ".hermes" / "skills",
    ]


def test_default_profile_is_kept_when_it_survives(project):
    existing = BridgeConfig(profiles={}, default_profile="auto-import")
    existing.profiles["auto-import"] = auto_import_config(AgentFormat.HERMES).profiles[
        "auto-import"
    ]

    updated = update_auto_import_config(existing, [AgentFormat.CURSOR])

    assert updated.default_profile == "auto-import"
    assert set(updated.profiles) == {"auto-import"}


def test_config_file_records_scopes(project):
    path = save_config(
        auto_import_config(AgentFormat.HERMES, scope="project"),
        project / "skill-bridge.yaml",
    )
    text = Path(path).read_text(encoding="utf-8")

    assert "scope: project" in text
    assert str(project / ".hermes" / "skills") in text
