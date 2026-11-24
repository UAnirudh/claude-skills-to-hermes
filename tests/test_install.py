"""`install` with several harnesses present: interactive picker and --all."""

import os
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from skill_bridge import agent_paths, cli
from skill_bridge.agent_paths import global_skill_root
from skill_bridge.cli import main
from skill_bridge.config import load_config
from skill_bridge.parser import find_skills
from skill_bridge.models import AgentFormat
from skill_bridge.sync import run_sync


def write_skill(root: Path, name: str, *, body: str = "# Demo\n") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name} converted across harnesses in tests.\n"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(agent_paths, "user_home", lambda: fake_home)
    monkeypatch.chdir(tmp_path)
    # Three harnesses, sorted as claude-code, cursor, hermes.
    for fmt in (AgentFormat.CLAUDE_CODE, AgentFormat.CURSOR, AgentFormat.HERMES):
        global_skill_root(fmt).mkdir(parents=True, exist_ok=True)
    return fake_home


def test_prompt_picks_one_harness_when_several_are_installed(home, monkeypatch):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "demo")
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)

    result = CliRunner().invoke(main, ["install"], input="3\n")

    assert result.exit_code == 0, result.output
    assert "1. claude-code" in result.output
    assert (home / ".hermes" / "skills" / "demo" / "SKILL.md").is_file()
    assert not (home / ".cursor" / "skills" / "demo").exists()

    profile = load_config(Path.cwd() / "skill-bridge.yaml").get_profile(None)
    assert [t.format for t in profile.targets] == [AgentFormat.HERMES]


def test_prompt_can_choose_all_harnesses(home, monkeypatch):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "demo")
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)

    result = CliRunner().invoke(main, ["install"], input="all\n")

    assert result.exit_code == 0, result.output
    for fmt in (AgentFormat.CURSOR, AgentFormat.HERMES):
        assert (global_skill_root(fmt) / "demo" / "SKILL.md").is_file()


def test_all_flag_installs_into_every_harness(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "demo")

    result = CliRunner().invoke(main, ["install", "--all"])

    assert result.exit_code == 0, result.output
    for fmt in (AgentFormat.CURSOR, AgentFormat.HERMES):
        assert (global_skill_root(fmt) / "demo" / "SKILL.md").is_file()

    profile = load_config(Path.cwd() / "skill-bridge.yaml").get_profile(None)
    assert {t.format for t in profile.targets} == {
        AgentFormat.CLAUDE_CODE,
        AgentFormat.CURSOR,
        AgentFormat.HERMES,
    }
    assert profile.auto_sources is True

    # The one profile really does keep every target current.
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "demo", body="# Demo\n\n## Follow-up\n")
    run_sync(config_path=Path.cwd() / "skill-bridge.yaml")
    for fmt in (AgentFormat.CURSOR, AgentFormat.HERMES):
        text = (global_skill_root(fmt) / "demo" / "SKILL.md").read_text(encoding="utf-8")
        assert "## Follow-up" in text


def test_all_installs_once_into_harnesses_sharing_a_skill_directory(home):
    # Codex and Amp use the same ~/.agents/skills folder.
    shared = global_skill_root(AgentFormat.CODEX)
    assert shared == global_skill_root(AgentFormat.AMP)
    shared.mkdir(parents=True, exist_ok=True)
    write_skill(global_skill_root(AgentFormat.HERMES), "demo")

    result = CliRunner().invoke(main, ["install", "--all"])

    assert result.exit_code == 0, result.output
    assert "share a skill directory" in result.output
    assert len(find_skills(shared)) == 1

    profile = load_config(Path.cwd() / "skill-bridge.yaml").get_profile(None)
    formats = [t.format for t in profile.targets]
    paths = [os.path.realpath(t.path) for t in profile.targets]
    assert len(paths) == len(set(paths))
    assert AgentFormat.AMP in formats
    assert AgentFormat.CODEX not in formats


def test_all_updates_an_existing_auto_import_profile(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "demo")
    first = CliRunner().invoke(main, ["install", "--all"])
    assert first.exit_code == 0, first.output

    # A fourth harness plus a profile the user added after the first install.
    global_skill_root(AgentFormat.CODEX).mkdir(parents=True)
    cfg_file = Path.cwd() / "skill-bridge.yaml"
    data = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    data["profiles"]["mine"] = data["profiles"]["auto-import"]
    cfg_file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    second = CliRunner().invoke(main, ["install", "--all"])

    assert second.exit_code == 0, second.output
    assert "Updated" in second.output
    assert "Keeping existing" not in second.output

    cfg = load_config(cfg_file)
    profile = cfg.get_profile(None)
    # The new ~/.agents/skills directory is represented once, as amp.
    assert {t.format for t in profile.targets} == {
        AgentFormat.CLAUDE_CODE,
        AgentFormat.AMP,
        AgentFormat.CURSOR,
        AgentFormat.HERMES,
    }
    assert "mine" in cfg.profiles
    assert (global_skill_root(AgentFormat.AMP) / "demo" / "SKILL.md").is_file()


def test_several_harnesses_without_a_tty_asks_for_harness_or_all(home):
    result = CliRunner().invoke(main, ["install"])

    assert result.exit_code == 1
    assert "--harness" in result.output
    assert "--all" in result.output
    assert not (Path.cwd() / "skill-bridge.yaml").exists()


def test_project_scope_detects_project_harnesses(tmp_path, monkeypatch, home):
    """The picker must offer project-local harnesses, not only global ones."""
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(project)
    write_skill(project / ".claude" / "skills", "claude-skill")
    write_skill(project / ".cursor" / "skills", "cursor-skill")
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)

    # Sorted: claude-code is 1, cursor is 2.
    result = CliRunner().invoke(main, ["install", "--scope", "project"], input="2\n")

    assert result.exit_code == 0, result.output
    assert (project / ".cursor" / "skills" / "claude-skill" / "SKILL.md").is_file()
    profile = load_config(project / "skill-bridge.yaml").get_profile(None)
    assert [t.path for t in profile.targets] == [project / ".cursor" / "skills"]


def test_scope_both_installs_and_keeps_both_copies_fresh(tmp_path, monkeypatch, home):
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(project)
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "global-skill")
    write_skill(project / ".cursor" / "skills", "project-skill")

    result = CliRunner().invoke(
        main, ["install", "--harness", "hermes", "--scope", "both"]
    )

    assert result.exit_code == 0, result.output
    profile = load_config(project / "skill-bridge.yaml").get_profile(None)
    assert profile.source_scope == "both"
    assert [t.path for t in profile.targets] == [
        global_skill_root(AgentFormat.HERMES),
        project / ".hermes" / "skills",
    ]

    # One sync fills both copies, from either scope.
    run_sync(config_path=project / "skill-bridge.yaml")
    for root in (global_skill_root(AgentFormat.HERMES), project / ".hermes" / "skills"):
        for name in ("global-skill", "project-skill"):
            assert (root / name / "SKILL.md").is_file(), (root, name)


def test_unknown_harness_is_a_clean_error(home):
    for args in (
        ["install", "--harness", "nope"],
        ["adopt", "--to", "nope"],
        ["paths", "nope"],
        ["convert", str(Path.cwd()), "--to", "nope"],
        ["reverse", "--from", "nope", "--to", "hermes"],
    ):
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 2, (args, result.output)
        assert "Unknown format 'nope'" in result.output
        assert "Traceback" not in result.output


def test_detect_shows_project_scope(tmp_path, monkeypatch, home):
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(project)
    write_skill(project / ".gemini" / "skills", "project-skill")

    global_only = CliRunner().invoke(main, ["detect"])
    both = CliRunner().invoke(main, ["detect", "--scope", "both"])

    assert global_only.exit_code == 0, global_only.output
    assert "(project)" not in global_only.output
    assert both.exit_code == 0, both.output
    assert "(project)" in both.output


def test_harness_and_all_together_are_rejected(home):
    result = CliRunner().invoke(main, ["install", "--harness", "hermes", "--all"])

    assert result.exit_code == 2
    assert "not both" in result.output


def test_all_with_no_harnesses_detected_explains_itself(tmp_path, monkeypatch):
    fake_home = tmp_path / "empty-home"
    fake_home.mkdir()
    monkeypatch.setattr(agent_paths, "user_home", lambda: fake_home)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["install", "--all"])

    assert result.exit_code == 1
    assert "No harness skill directories found" in result.output


def test_all_with_to_path_is_rejected(home):
    result = CliRunner().invoke(main, ["install", "--all", "--to-path", str(Path.cwd())])

    assert result.exit_code == 2
    assert "--to-path" in result.output
