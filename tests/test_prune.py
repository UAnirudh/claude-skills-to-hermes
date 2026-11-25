"""Pruning and uninstalling generated copies, safely.

The contract: a copy whose recorded source was deleted or renamed is removed;
a skill the harness wrote itself is never touched. The provenance marker is the
only thing that separates the two.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from click.testing import CliRunner

from skill_bridge import agent_paths
from skill_bridge.agent_paths import global_skill_root
from skill_bridge.cli import main
from skill_bridge.config import (
    SkillLocation,
    SyncProfile,
    auto_import_config,
    load_config,
    save_config,
)
from skill_bridge.models import AgentFormat
from skill_bridge.prune import plan_removals, source_disappeared
from skill_bridge.provenance import write_marker
from skill_bridge.sync import collect_source_skills, run_prune, run_sync


def write_skill(root: Path, name: str, *, title: str | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {title or name}\n"
        f"description: {name} used by the prune tests.\n"
        "---\n\n"
        "# Demo\n",
        encoding="utf-8",
    )
    return skill_dir


def write_config(tmp_path: Path, source: Path, target: Path) -> Path:
    config = {
        "default_profile": "test",
        "profiles": {
            "test": {
                "source": {"format": "generic", "path": str(source)},
                "targets": [{"format": "hermes", "path": str(target)}],
                "watch": True,
                "validate": True,
            }
        },
    }
    path = tmp_path / "skill-bridge.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def test_prune_removes_the_copy_of_a_deleted_source(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    assert (target / "demo" / "SKILL.md").is_file()

    shutil.rmtree(source / "demo")
    outcome = run_prune(config_path=config)

    assert [item.skill_name for item in outcome.removed] == ["demo"]
    assert not (target / "demo").exists()


def test_cli_sync_prune_removes_a_renamed_copy_and_imports_the_new_name(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)

    # Rename both the folder and the frontmatter name.
    (source / "demo").rename(source / "renamed")
    (source / "renamed" / "SKILL.md").write_text(
        "---\nname: renamed\ndescription: The renamed skill.\n---\n\n# Renamed\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main, ["sync", "--config", str(config), "--prune"]
    )

    assert result.exit_code == 0, result.output
    assert "Pruned 1 skill(s)" in result.output
    assert not (target / "demo").exists()
    assert (target / "renamed" / "SKILL.md").is_file()


def test_prune_removes_the_old_copy_when_only_the_skill_name_changed(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    skill = write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    assert (target / "demo").is_dir()

    # Same folder, new frontmatter name: the output directory changes name.
    (skill / "SKILL.md").write_text(
        "---\nname: demo-two\ndescription: Renamed in place.\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    run_sync(config_path=config)
    outcome = run_prune(config_path=config)

    assert [item.skill_name for item in outcome.removed] == ["demo"]
    assert not (target / "demo").exists()
    assert (target / "demo-two" / "SKILL.md").is_file()


def test_prune_never_removes_a_hand_written_skill(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)

    native = write_skill(target, "hand-written")
    shutil.rmtree(source / "demo")
    outcome = run_prune(config_path=config)

    assert [item.skill_name for item in outcome.removed] == ["demo"]
    assert native.is_dir()
    assert native in outcome.plan.kept_native


def test_prune_keeps_copies_whose_source_still_exists(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)

    outcome = run_prune(config_path=config)

    assert outcome.plan.items == []
    assert (target / "demo" / "SKILL.md").is_file()


def test_prune_dry_run_reports_without_removing_or_syncing(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    shutil.rmtree(source / "demo")

    result = CliRunner().invoke(
        main, ["sync", "--config", str(config), "--prune", "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "Would prune 1 skill(s)" in result.output
    assert "Dry run: sync skipped" in result.output
    assert (target / "demo" / "SKILL.md").is_file()


def test_dry_run_without_prune_is_rejected(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    config = write_config(tmp_path, source, target)

    result = CliRunner().invoke(main, ["sync", "--config", str(config), "--dry-run"])

    assert result.exit_code == 2
    assert "--prune" in result.output


def test_a_reappearing_source_is_imported_again(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    shutil.rmtree(source / "demo")
    run_prune(config_path=config)
    assert not (target / "demo").exists()

    write_skill(source, "demo")
    run_sync(config_path=config)

    assert (target / "demo" / "SKILL.md").is_file()
    assert source_disappeared(target / "demo") is None


def test_uninstall_removes_generated_copies_and_keeps_native_ones(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    native = write_skill(target, "hand-written")

    preview = CliRunner().invoke(
        main,
        ["uninstall", "--harness", "hermes", "--to-path", str(target), "--dry-run"],
    )

    assert preview.exit_code == 0, preview.output
    assert "would remove" in preview.output
    assert "hand-written" in preview.output
    assert (target / "demo").is_dir()

    result = CliRunner().invoke(
        main, ["uninstall", "--harness", "hermes", "--to-path", str(target)]
    )

    assert result.exit_code == 0, result.output
    assert not (target / "demo").exists()
    assert native.is_dir()
    assert "Removed 1 skill(s)" in result.output


def test_uninstall_stale_only_keeps_copies_whose_source_exists(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "gone")
    write_skill(source, "kept")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    shutil.rmtree(source / "gone")

    result = CliRunner().invoke(
        main,
        [
            "uninstall",
            "--harness",
            "hermes",
            "--to-path",
            str(target),
            "--stale-only",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (target / "gone").exists()
    assert (target / "kept" / "SKILL.md").is_file()


def test_uninstall_needs_an_explicit_target(tmp_path):
    bare = CliRunner().invoke(main, ["uninstall"])
    both = CliRunner().invoke(
        main, ["uninstall", "--harness", "hermes", "--all"]
    )

    assert bare.exit_code == 2
    assert "not both" in both.output


def test_a_generated_copy_without_a_source_path_is_only_removed_by_uninstall(tmp_path):
    target = tmp_path / "tgt"
    ghost = write_skill(target, "ghost")
    write_marker(
        ghost,
        source_format=AgentFormat.GENERIC,
        target_format=AgentFormat.HERMES,
    )
    locations = [SkillLocation(format=AgentFormat.HERMES, path=target)]

    assert plan_removals(locations).items == []
    assert [item.skill_name for item in plan_removals(locations, stale_only=False).items] == [
        "ghost"
    ]


def test_a_stale_generated_copy_is_not_used_as_a_source(tmp_path):
    root = tmp_path / "skills"
    ghost = write_skill(root, "ghost")
    write_marker(
        ghost,
        source_format=AgentFormat.GENERIC,
        target_format=AgentFormat.HERMES,
        source_path=tmp_path / "deleted-source",
    )
    write_skill(root, "real")

    entries = collect_source_skills(
        [SkillLocation(format=AgentFormat.GENERIC, path=root)]
    )

    assert [skill_dir.name for _, skill_dir in entries] == ["real"]


def test_deleting_a_source_does_not_resurrect_it_into_another_harness(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(agent_paths, "user_home", lambda: home)
    claude = global_skill_root(AgentFormat.CLAUDE_CODE)
    write_skill(claude, "native")

    config = tmp_path / "skill-bridge.yaml"
    save_config(
        auto_import_config([AgentFormat.HERMES, AgentFormat.CURSOR]), config
    )
    run_sync(config_path=config)
    for fmt in (AgentFormat.HERMES, AgentFormat.CURSOR):
        assert (global_skill_root(fmt) / "native" / "SKILL.md").is_file()

    shutil.rmtree(claude / "native")

    # Both remaining copies are generated from the deleted skill, so neither
    # may act as a source for the other.
    try:
        run_sync(config_path=config)
    except FileNotFoundError:
        pass
    else:  # pragma: no cover - a resurrection would put skills back
        raise AssertionError("a stale generated copy was imported as a source")

    outcome = run_prune(config_path=config)

    assert sorted(item.skill_name for item in outcome.removed) == ["native", "native"]
    for fmt in (AgentFormat.HERMES, AgentFormat.CURSOR):
        assert not (global_skill_root(fmt) / "native").exists()


def test_uninstall_drops_the_profile_so_a_later_sync_cannot_recreate(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    # load_config resolves the last candidate through Path.home().
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(agent_paths, "user_home", lambda: home)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    write_skill(project / ".claude" / "skills", "demo")

    runner = CliRunner()
    installed = runner.invoke(
        main, ["install", "--harness", "hermes", "--scope", "project"]
    )
    assert installed.exit_code == 0, installed.output
    copy = project / ".hermes" / "skills" / "demo"
    config = project / "skill-bridge.yaml"
    assert (copy / "SKILL.md").is_file()
    assert config.is_file()

    result = runner.invoke(
        main, ["uninstall", "--harness", "hermes", "--scope", "project"]
    )

    assert result.exit_code == 0, result.output
    assert "Removed 1 skill(s)" in result.output
    assert not copy.exists()
    # The profile that produced the copy goes with it...
    assert not config.exists()

    # ...so the next sync has nothing left to recreate them from.
    synced = runner.invoke(main, ["sync"])
    assert synced.exit_code == 1, synced.output
    assert "No skill-bridge.yaml found" in synced.output
    assert not copy.exists()


def test_uninstall_only_forgets_the_target_it_emptied(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    # 'sources: auto' resolves harness directories from the working directory.
    monkeypatch.chdir(project)
    write_skill(project / ".claude" / "skills", "demo")
    config = project / "skill-bridge.yaml"
    save_config(
        auto_import_config(
            [AgentFormat.HERMES, AgentFormat.CURSOR],
            scope="project",
            project=project,
        ),
        config,
    )
    # A profile the user wrote themselves must survive untouched.
    cfg = load_config(config, project=project)
    cfg.profiles["mine"] = SyncProfile(
        name="mine",
        source=SkillLocation(
            format=AgentFormat.GENERIC, path=project / ".claude" / "skills"
        ),
        targets=[SkillLocation(format=AgentFormat.GENERIC, path=project / "out")],
    )
    save_config(cfg, config)
    run_sync(config_path=config)
    hermes_copy = project / ".hermes" / "skills" / "demo"
    cursor_copy = project / ".cursor" / "skills" / "demo"
    assert (hermes_copy / "SKILL.md").is_file()
    assert (cursor_copy / "SKILL.md").is_file()

    result = CliRunner().invoke(
        main, ["uninstall", "--harness", "hermes", "--scope", "project"]
    )

    assert result.exit_code == 0, result.output
    assert not hermes_copy.exists()
    assert (cursor_copy / "SKILL.md").is_file()
    assert config.is_file()
    after = load_config(config, project=project)
    assert "mine" in after.profiles
    assert [t.format for t in after.profiles["auto-import"].targets] == [
        AgentFormat.CURSOR
    ]


def test_stale_only_uninstall_leaves_the_profile_alone(tmp_path):
    source, target = tmp_path / "src", tmp_path / "tgt"
    write_skill(source, "demo")
    config = write_config(tmp_path, source, target)
    run_sync(config_path=config)
    shutil.rmtree(source / "demo")

    result = CliRunner().invoke(
        main,
        [
            "uninstall",
            "--harness",
            "hermes",
            "--to-path",
            str(target),
            "--stale-only",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (target / "demo").exists()
    # That harness is still a target, so sync must keep refreshing it.
    after = load_config(config, project=tmp_path)
    assert [t.path for t in after.profiles["test"].targets] == [target]


def test_uninstall_forgets_a_profile_stored_outside_the_default_locations(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    write_skill(project / ".claude" / "skills", "demo")
    # Deliberately somewhere load_config would never look.
    elsewhere = tmp_path / "conf" / "bridge.yaml"
    elsewhere.parent.mkdir()

    runner = CliRunner()
    installed = runner.invoke(
        main,
        [
            "install",
            "--harness",
            "hermes",
            "--scope",
            "project",
            "--config",
            str(elsewhere),
        ],
    )
    assert installed.exit_code == 0, installed.output
    copy = project / ".hermes" / "skills" / "demo"
    assert (copy / "SKILL.md").is_file()
    assert elsewhere.is_file()

    result = runner.invoke(
        main,
        [
            "uninstall",
            "--harness",
            "hermes",
            "--scope",
            "project",
            "--config",
            str(elsewhere),
        ],
    )

    assert result.exit_code == 0, result.output
    assert not copy.exists()
    assert not elsewhere.exists()

    # The explicit sync still cannot bring the copies back.
    synced = runner.invoke(main, ["sync", "--config", str(elsewhere)])
    assert synced.exit_code != 0, synced.output
    assert not copy.exists()


def test_uninstall_leaves_a_config_it_was_not_pointed_at(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    write_skill(project / ".claude" / "skills", "demo")
    elsewhere = tmp_path / "conf" / "bridge.yaml"
    elsewhere.parent.mkdir()
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "install",
            "--harness",
            "hermes",
            "--scope",
            "project",
            "--config",
            str(elsewhere),
        ],
    )

    # Without --config the uninstall only knows the defaults, which hold no
    # profile for this project: the copies go, the external file is untouched.
    result = runner.invoke(
        main, ["uninstall", "--harness", "hermes", "--scope", "project"]
    )

    assert result.exit_code == 0, result.output
    assert not (project / ".hermes" / "skills" / "demo").exists()
    assert elsewhere.is_file()
