"""Automatic cross-harness import: any harness -> your harness."""

import os
from pathlib import Path

import pytest

from skill_bridge import agent_paths
from skill_bridge.adopt import plan_adopt, run_adopt, source_roots
from skill_bridge.agent_paths import global_skill_root
from skill_bridge.config import auto_import_config, load_config, save_config
from skill_bridge.models import AgentFormat
from skill_bridge.provenance import is_bridge_generated, read_marker
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
    return fake_home


def test_imports_from_every_other_harness(home):
    expected = {
        AgentFormat.CLAUDE_CODE: "claude-helper",
        AgentFormat.CURSOR: "cursor-helper",
        AgentFormat.GEMINI: "gemini-helper",
        AgentFormat.COPILOT: "copilot-helper",
        AgentFormat.OPENCODE: "opencode-helper",
        AgentFormat.CODEX: "codex-helper",
    }
    for fmt, name in expected.items():
        write_skill(global_skill_root(fmt), name)

    report = run_adopt(AgentFormat.HERMES)

    assert report.target_root == global_skill_root(AgentFormat.HERMES)
    imported = {item.skill_name for item in report.items if item.action == "import"}
    assert imported == set(expected.values())

    for name in expected.values():
        dest = report.target_root / name
        assert (dest / "SKILL.md").is_file()
        assert is_bridge_generated(dest)
        assert read_marker(dest)["target-format"] == "hermes"


def test_shared_agents_folder_is_not_copied_into_itself(home):
    write_skill(global_skill_root(AgentFormat.CODEX), "shared-skill")

    report = plan_adopt(AgentFormat.AMP)  # both use ~/.agents/skills

    assert report.items == []
    target_real = os.path.realpath(global_skill_root(AgentFormat.AMP))
    assert all(os.path.realpath(root) != target_real for _, root in source_roots(AgentFormat.AMP))


def test_duplicate_names_are_imported_once(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "same-skill")
    write_skill(global_skill_root(AgentFormat.CURSOR), "same-skill")

    report = run_adopt(AgentFormat.HERMES)

    assert report.count("import") == 1
    assert report.count("skip") == 1


def test_native_skill_is_protected_unless_forced(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "protected", body="# Upstream\n")
    native = write_skill(global_skill_root(AgentFormat.HERMES), "protected", body="# Mine\n")

    report = run_adopt(AgentFormat.HERMES)
    assert report.count("skip") == 1
    assert "# Mine" in (native / "SKILL.md").read_text(encoding="utf-8")

    forced = run_adopt(AgentFormat.HERMES, force=True)
    assert forced.count("overwrite") == 1
    assert "# Upstream" in (native / "SKILL.md").read_text(encoding="utf-8")


def test_repeat_runs_update_instead_of_duplicating(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "refresh-me")

    first = run_adopt(AgentFormat.HERMES)
    assert first.count("import") == 1

    second = run_adopt(AgentFormat.HERMES)
    assert second.count("import") == 0
    assert second.count("update") == 1
    assert len(list(first.target_root.iterdir())) == 1


def test_dry_run_writes_nothing(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "preview-me")

    report = run_adopt(AgentFormat.HERMES, dry_run=True)

    assert report.count("import") == 1
    assert not (report.target_root / "preview-me").exists()


def test_from_filter_limits_sources(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "claude-only")
    write_skill(global_skill_root(AgentFormat.GEMINI), "gemini-only")

    report = run_adopt(AgentFormat.OPENCODE, from_formats=[AgentFormat.GEMINI])

    assert report.skill_names(written_only=True) == ["gemini-only"]


def test_auto_import_config_round_trip_and_sync(home, tmp_path):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "alpha")
    write_skill(global_skill_root(AgentFormat.CURSOR), "beta")

    cfg = auto_import_config(AgentFormat.OPENCODE)
    path = save_config(cfg, tmp_path / "skill-bridge.yaml")
    loaded = load_config(path)

    profile = loaded.get_profile(None)
    assert profile.auto_sources is True
    assert profile.targets[0].format is AgentFormat.OPENCODE

    results = run_sync(config_path=path)
    assert {r.skill_name for r in results} == {"alpha", "beta"}
    target = global_skill_root(AgentFormat.OPENCODE)
    assert (target / "alpha" / "SKILL.md").is_file()
    assert (target / "beta" / "SKILL.md").is_file()


def test_project_scope_import(tmp_path, monkeypatch, home):
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(project)
    write_skill(project / ".cursor" / "skills", "project-skill")

    report = run_adopt(AgentFormat.CLAUDE_CODE, scope="project")

    assert report.target_root == project / ".claude" / "skills"
    assert (report.target_root / "project-skill" / "SKILL.md").is_file()


@pytest.mark.parametrize(
    "fmt,suffix",
    [
        (AgentFormat.CLAUDE_CODE, ".claude/skills"),
        (AgentFormat.HERMES, ".hermes/skills"),
        (AgentFormat.CODEX, ".agents/skills"),
        (AgentFormat.CURSOR, ".cursor/skills"),
        (AgentFormat.WINDSURF, ".codeium/windsurf/skills"),
        (AgentFormat.CLINE, ".cline/skills"),
        (AgentFormat.OPENCODE, ".config/opencode/skills"),
        (AgentFormat.GEMINI, ".gemini/skills"),
        (AgentFormat.COPILOT, ".copilot/skills"),
        (AgentFormat.GOOSE, ".config/goose/skills"),
        (AgentFormat.OPENHANDS, ".openhands/skills"),
        (AgentFormat.ROO, ".roo/skills"),
        (AgentFormat.CONTINUE, ".continue/skills"),
        (AgentFormat.AMP, ".agents/skills"),
    ],
)
def test_global_install_paths(fmt, suffix):
    assert str(global_skill_root(fmt)).replace("\\", "/").endswith(suffix)


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("gemini-cli", AgentFormat.GEMINI),
        ("github-copilot", AgentFormat.COPILOT),
        ("open-code", AgentFormat.OPENCODE),
        ("goose-cli", AgentFormat.GOOSE),
        ("zed", AgentFormat.AMP),
        ("continue-dev", AgentFormat.CONTINUE),
    ],
)
def test_harness_aliases(alias, expected):
    assert AgentFormat.from_string(alias) is expected
