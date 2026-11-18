from pathlib import Path

import pytest
from click.testing import CliRunner

from skill_bridge.cli import main
from skill_bridge.config import SkillLocation, SyncProfile
from skill_bridge.models import AgentFormat
from skill_bridge.watch import SkillWatchHandler, watch_from_config


def _make_skill(root: Path, name: str = "demo-skill") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo skill used by the watch tests.\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    return skill


def test_watch_handler_syncs_existing_skills(tmp_path):
    source = tmp_path / "source"
    _make_skill(source)
    target = tmp_path / "target"

    profile = SyncProfile(
        name="auto-import",
        source=SkillLocation(format=AgentFormat.GENERIC, path=source, scope="custom"),
        targets=[SkillLocation(format=AgentFormat.OPENCODE, path=target, scope="custom")],
        watch=False,
    )

    seen = []
    handler = SkillWatchHandler(profile, on_sync=lambda reason, results: seen.append(results))
    handler.run()

    assert (target / "demo-skill" / "SKILL.md").is_file()
    assert len(seen) == 1
    assert len(seen[0]) == 1


def test_watch_handler_empty_source_is_not_an_error(tmp_path):
    source = tmp_path / "empty"
    source.mkdir()
    profile = SyncProfile(
        name="auto-import",
        source=SkillLocation(format=AgentFormat.OPENCODE, path=source, scope="custom"),
        targets=[SkillLocation(format=AgentFormat.HERMES, path=tmp_path / "target", scope="custom")],
    )

    synced: list = []
    errors: list = []
    handler = SkillWatchHandler(
        profile,
        on_sync=lambda reason, results: synced.append((reason, results)),
        on_error=lambda reason, exc: errors.append(exc),
    )

    assert handler.run("initial") == []
    assert errors == []
    assert synced == [("initial", [])]


def test_watch_cli_reports_empty_source_as_information(tmp_path, monkeypatch):
    config = tmp_path / "skill-bridge.yaml"
    config.write_text(
        "default_profile: auto-import\n"
        "profiles:\n"
        "  auto-import:\n"
        "    source:\n"
        "      format: opencode\n"
        "      path: " + str(tmp_path / "empty") + "\n"
        "    targets:\n"
        "      - format: hermes\n"
        "        path: " + str(tmp_path / "target") + "\n"
        "    watch: true\n",
        encoding="utf-8",
    )

    def fake_watch(**kwargs):
        kwargs["on_sync"]("initial", [])

    monkeypatch.setattr("skill_bridge.cli.watch_from_config", fake_watch)

    result = CliRunner().invoke(main, ["watch", "-c", str(config)])

    assert result.exit_code == 0, result.output
    assert "No skills to sync yet" in result.output
    assert "Sync failed" not in result.output


def test_watch_from_config_honours_watch_false(tmp_path, monkeypatch):
    config = tmp_path / "skill-bridge.yaml"
    config.write_text(
        "default_profile: auto-import\n"
        "profiles:\n"
        "  auto-import:\n"
        "    source:\n"
        "      format: hermes\n"
        "      path: ~/.hermes/skills\n"
        "    targets:\n"
        "      - format: claude-code\n"
        "        path: ~/.claude/skills\n"
        "    watch: false\n",
        encoding="utf-8",
    )

    seen = {}
    monkeypatch.setattr(
        "skill_bridge.watch.watch_profile",
        lambda profile, **kwargs: seen.setdefault("profile", profile),
    )

    watch_from_config("auto-import", config, initial_sync=False, force=True)

    assert seen["profile"].name == "auto-import"
    assert seen["profile"].watch is False


def test_watch_from_config_refuses_watch_false_without_force(tmp_path):
    config = tmp_path / "skill-bridge.yaml"
    config.write_text(
        "default_profile: auto-import\n"
        "profiles:\n"
        "  auto-import:\n"
        "    source:\n"
        "      format: hermes\n"
        "      path: ~/.hermes/skills\n"
        "    targets: []\n"
        "    watch: false\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="watch: false"):
        watch_from_config("auto-import", config, initial_sync=False)
