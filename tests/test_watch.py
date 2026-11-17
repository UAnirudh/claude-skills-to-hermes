"""Watch mode must work with the profiles `install` writes."""

import os
from pathlib import Path

import pytest

from skill_bridge import agent_paths, watch
from skill_bridge.agent_paths import global_skill_root
from skill_bridge.config import SkillLocation, SyncProfile, auto_import_config
from skill_bridge.models import AgentFormat


def write_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name} watched across harnesses in tests.\n"
        "---\n\n# Watched\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(agent_paths, "user_home", lambda: fake_home)
    return fake_home


class FakeEventHandler:
    def __init__(self) -> None:
        pass


class FakeObserver:
    def __init__(self) -> None:
        self.scheduled: list[str] = []
        self.started = False
        self.stopped = False
        self.joined = False

    def schedule(self, handler, path, recursive=False):  # noqa: ARG002
        self.scheduled.append(path)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self) -> None:
        self.joined = True


def interrupt_sleep(_seconds: float) -> None:
    raise KeyboardInterrupt


def test_auto_profile_watches_every_source_harness(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "a")
    write_skill(global_skill_root(AgentFormat.GEMINI), "b")

    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)
    roots = watch.watch_roots(profile)

    assert set(roots) == {
        global_skill_root(AgentFormat.CLAUDE_CODE),
        global_skill_root(AgentFormat.GEMINI),
    }
    assert global_skill_root(AgentFormat.HERMES) not in roots


def test_auto_profile_ignores_shared_target_folder(home):
    """Amp and Codex both use ~/.agents/skills: never watch our own output."""
    write_skill(global_skill_root(AgentFormat.AMP), "shared")

    profile = auto_import_config(AgentFormat.CODEX).get_profile(None)

    assert watch.watch_roots(profile) == []


def test_cline_has_its_own_folder_and_is_watched(home):
    write_skill(global_skill_root(AgentFormat.CLINE), "cline-skill")

    profile = auto_import_config(AgentFormat.CODEX).get_profile(None)

    assert watch.watch_roots(profile) == [global_skill_root(AgentFormat.CLINE)]


def test_single_source_profile_still_works(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "x")

    profile = SyncProfile(
        name="legacy",
        source=SkillLocation(
            format=AgentFormat.CLAUDE_CODE,
            path=global_skill_root(AgentFormat.CLAUDE_CODE),
        ),
        targets=[
            SkillLocation(format=AgentFormat.HERMES, path=global_skill_root(AgentFormat.HERMES))
        ],
    )

    assert watch.watch_roots(profile) == [global_skill_root(AgentFormat.CLAUDE_CODE)]


def test_missing_source_directories_are_skipped(home):
    write_skill(global_skill_root(AgentFormat.ROO), "present")

    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)

    assert watch.watch_roots(profile) == [global_skill_root(AgentFormat.ROO)]


def test_watch_profile_schedules_all_auto_sources(home, monkeypatch):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "one")
    write_skill(global_skill_root(AgentFormat.GEMINI), "two")
    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)

    observer = FakeObserver()
    monkeypatch.setattr(watch, "_try_watchdog", lambda: (FakeEventHandler, lambda: observer))
    monkeypatch.setattr(watch.time, "sleep", interrupt_sleep)

    watch.watch_profile(profile)

    assert sorted(observer.scheduled) == sorted(
        [
            str(global_skill_root(AgentFormat.CLAUDE_CODE)),
            str(global_skill_root(AgentFormat.GEMINI)),
        ]
    )
    assert observer.started and observer.stopped and observer.joined


def test_watch_survives_having_no_sources_yet(home, monkeypatch):
    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)

    observer = FakeObserver()
    monkeypatch.setattr(watch, "_try_watchdog", lambda: (FakeEventHandler, lambda: observer))
    monkeypatch.setattr(watch.time, "sleep", interrupt_sleep)

    watch.watch_profile(profile)

    assert observer.scheduled == []
    assert observer.stopped


def test_trigger_syncs_auto_profile(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "auto-skill")
    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)

    seen = []
    handler = watch.SkillWatchHandler(profile, on_sync=lambda reason, results: seen.append(results))
    handler.trigger("SKILL.md")

    assert [r.skill_name for r in seen[0]] == ["auto-skill"]
    assert (global_skill_root(AgentFormat.HERMES) / "auto-skill" / "SKILL.md").is_file()


def test_trigger_reports_errors_without_dying(home):
    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)  # no sources at all

    errors = []
    handler = watch.SkillWatchHandler(profile, on_error=errors.append)
    handler.trigger("SKILL.md")

    assert errors and isinstance(errors[0], FileNotFoundError)
    assert isinstance(handler.last_error, FileNotFoundError)


class FakeEvent:
    def __init__(self, event_type, src_path, is_directory=False):
        self.event_type = event_type
        self.src_path = src_path
        self.is_directory = is_directory


def test_reading_a_source_file_is_not_a_change():
    """Syncing reads SKILL.md; a read event must not re-trigger the sync."""
    path = "/home/.claude/skills/demo/SKILL.md"

    assert not watch.is_relevant_event(FakeEvent("opened", path))
    assert not watch.is_relevant_event(FakeEvent("closed_no_write", path))
    assert not watch.is_relevant_event(FakeEvent("closed", path))


def test_write_events_are_changes():
    skill_md = "/home/.claude/skills/demo/SKILL.md"

    assert watch.is_relevant_event(FakeEvent("modified", skill_md))
    assert watch.is_relevant_event(FakeEvent("created", skill_md))
    assert watch.is_relevant_event(FakeEvent("deleted", skill_md))
    assert watch.is_relevant_event(
        FakeEvent("modified", "/home/.claude/skills/demo/references/guide.md")
    )


def test_unrelated_files_and_directories_are_ignored():
    assert not watch.is_relevant_event(
        FakeEvent("modified", "/home/.claude/skills/demo/notes.txt")
    )
    assert not watch.is_relevant_event(
        FakeEvent("created", "/home/.claude/skills/demo", is_directory=True)
    )


def test_watch_from_config_with_install_written_config(home, tmp_path, monkeypatch):
    write_skill(global_skill_root(AgentFormat.COPILOT), "from-copilot")
    config_path = tmp_path / "skill-bridge.yaml"
    config_path.write_text(
        "default_profile: auto-import\n"
        "profiles:\n"
        "  auto-import:\n"
        "    sources: auto\n"
        "    targets:\n"
        "      - format: hermes\n"
        f"        path: {global_skill_root(AgentFormat.HERMES)}\n"
        "    watch: true\n",
        encoding="utf-8",
    )

    observer = FakeObserver()
    monkeypatch.setattr(watch, "_try_watchdog", lambda: (FakeEventHandler, lambda: observer))
    monkeypatch.setattr(watch.time, "sleep", interrupt_sleep)

    watch.watch_from_config(config_path=config_path)

    assert observer.scheduled == [str(global_skill_root(AgentFormat.COPILOT))]
