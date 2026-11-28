"""Watch mode must work with the profiles `install` writes."""

import os
import shutil
import threading
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
        self.unscheduled: list[str] = []
        self.started = False
        self.stopped = False
        self.joined = False
        self.alive = True

    def schedule(self, handler, path, recursive=False):  # noqa: ARG002
        self.scheduled.append(path)
        return path

    def unschedule(self, watch) -> None:
        self.unscheduled.append(watch)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self) -> None:
        self.joined = True

    def is_alive(self) -> bool:
        return self.alive


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


def test_project_scope_auto_profile_watches_project_sources(home, tmp_path, monkeypatch):
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(project)
    write_skill(project / ".cursor" / "skills", "project-skill")

    profile = auto_import_config(AgentFormat.HERMES, scope="project").get_profile(None)

    assert watch.watch_roots(profile) == [project / ".cursor" / "skills"]


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


def test_an_event_during_a_sync_waits_for_it_instead_of_racing_it(home, monkeypatch):
    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)
    started = threading.Event()
    release = threading.Event()
    runs: list[str] = []

    def slow_sync(_profile):
        runs.append("run")
        started.set()
        release.wait(5)
        return []

    monkeypatch.setattr(watch, "sync_profile", slow_sync)
    handler = watch.SkillWatchHandler(profile, debounce_seconds=0.0)

    worker = threading.Thread(target=handler.trigger, args=("first",))
    worker.start()
    assert started.wait(5)
    handler.trigger("second")  # arrives while the first sync is still running
    release.set()
    worker.join(5)

    assert len(runs) == 1  # never two syncs at once
    handler.flush_pending()
    assert len(runs) == 2  # and the event was not lost


def test_a_callback_that_raises_does_not_stop_the_watcher(home):
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "watched")
    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)

    def closed_pipe(reason, results):
        raise BrokenPipeError("stdout closed")

    handler = watch.SkillWatchHandler(
        profile, debounce_seconds=0.0, on_sync=closed_pipe
    )
    handler.trigger("first")

    assert isinstance(handler.last_error, BrokenPipeError)

    seen = []
    handler.on_sync = lambda reason, results: seen.append(results)
    handler.trigger("second", force=True)

    assert [result.skill_name for result in seen[0]] == ["watched"]
    assert handler.last_error is None


def test_a_dead_observer_stops_the_loop_instead_of_watching_nothing(home, monkeypatch):
    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)
    observer = FakeObserver()
    observer.alive = False
    monkeypatch.setattr(
        watch, "_try_watchdog", lambda: (FakeEventHandler, lambda: observer)
    )
    monkeypatch.setattr(watch.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="observer"):
        watch.watch_profile(profile)

    assert observer.stopped and observer.joined


def test_a_deleted_root_is_forgotten_so_it_can_be_watched_again(home):
    claude = global_skill_root(AgentFormat.CLAUDE_CODE)
    write_skill(claude, "one")
    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)
    observer = FakeObserver()
    roots = watch._RootWatcher(observer, object(), profile)
    assert roots.refresh() is True

    shutil.rmtree(claude)
    roots.forget(str(claude))  # watchdog reports the root's own deletion
    assert observer.unscheduled == [str(claude)]

    # Recreated before the rescan that would have noticed it missing: the
    # forgotten watch is what lets it be armed again.
    write_skill(claude, "back")
    assert roots.refresh() is True
    assert observer.scheduled == [str(claude), str(claude)]


def test_a_source_root_that_is_recreated_is_watched_again(home, monkeypatch):
    claude = global_skill_root(AgentFormat.CLAUDE_CODE)
    write_skill(claude, "one")
    profile = auto_import_config(AgentFormat.HERMES).get_profile(None)

    observer = FakeObserver()
    monkeypatch.setattr(
        watch, "_try_watchdog", lambda: (FakeEventHandler, lambda: observer)
    )
    monkeypatch.setattr(watch, "RESCAN_SECONDS", 0.0)
    ticks: list[int] = []

    def sleep(_seconds):
        ticks.append(1)
        if len(ticks) == 1:
            shutil.rmtree(claude)  # a harness directory is deleted ...
        elif len(ticks) == 2:
            write_skill(claude, "back")  # ... and created again
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(watch.time, "sleep", sleep)

    watch.watch_profile(profile)

    assert observer.scheduled == [str(claude), str(claude)]
    assert observer.unscheduled == [os.path.realpath(claude)]


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


def test_install_all_profile_watches_the_targets_it_imports_from(home):
    """`install --all` targets every harness, so those are the only sources."""
    formats = [AgentFormat.CLAUDE_CODE, AgentFormat.CURSOR, AgentFormat.HERMES]
    for fmt in formats:
        write_skill(global_skill_root(fmt), fmt.value)

    profile = auto_import_config(formats).get_profile(None)

    assert watch.watch_roots(profile) == [global_skill_root(fmt) for fmt in formats]


def test_watch_profile_schedules_every_all_target(home, monkeypatch):
    formats = [AgentFormat.CLAUDE_CODE, AgentFormat.CURSOR, AgentFormat.HERMES]
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "fan-out")
    # `install --all` only targets harnesses whose skill directory exists.
    for fmt in formats[1:]:
        global_skill_root(fmt).mkdir(parents=True, exist_ok=True)
    profile = auto_import_config(formats).get_profile(None)

    observer = FakeObserver()
    monkeypatch.setattr(watch, "_try_watchdog", lambda: (FakeEventHandler, lambda: observer))
    monkeypatch.setattr(watch.time, "sleep", interrupt_sleep)

    watch.watch_profile(profile)

    assert sorted(observer.scheduled) == sorted(
        str(global_skill_root(fmt)) for fmt in formats
    )


def test_sync_own_writes_do_not_retrigger_the_watch(home):
    """Fanning a change out must not feed the writes back in as new changes."""
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "fan-out")
    profile = auto_import_config(
        [AgentFormat.CLAUDE_CODE, AgentFormat.CURSOR, AgentFormat.HERMES]
    ).get_profile(None)

    handler = watch.SkillWatchHandler(profile, debounce_seconds=0)
    handler.trigger("SKILL.md")

    written = global_skill_root(AgentFormat.HERMES) / "fan-out" / "SKILL.md"
    assert written.is_file()
    generated = [
        path
        for root in (global_skill_root(AgentFormat.CURSOR), global_skill_root(AgentFormat.HERMES))
        for path in root.rglob("*.md")
    ]
    assert generated
    for path in generated:
        assert not handler.should_react(FakeEvent("modified", str(path)))
        assert not handler.should_react(FakeEvent("created", str(path)))

    # A real edit afterwards (different contents) is a change again.
    written.write_text(written.read_text(encoding="utf-8") + "\n## Edited\n", encoding="utf-8")
    assert handler.should_react(FakeEvent("modified", str(written)))


def test_edit_in_any_watched_harness_fans_out_from_that_harness(home):
    """The watched root is also a target: an edit there still fans out.

    It must fan out *from the edited copy*: sync otherwise imports each skill
    name from the first harness that has it, which would overwrite the edit.
    """
    write_skill(global_skill_root(AgentFormat.CLAUDE_CODE), "fan-out")
    write_skill(global_skill_root(AgentFormat.CURSOR), "fan-out")
    profile = auto_import_config(
        [AgentFormat.CLAUDE_CODE, AgentFormat.CURSOR, AgentFormat.HERMES]
    ).get_profile(None)

    skill_md = global_skill_root(AgentFormat.CURSOR) / "fan-out" / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8") + "\n## From cursor\n", encoding="utf-8"
    )

    handler = watch.SkillWatchHandler(profile, debounce_seconds=0)
    assert handler.should_react(FakeEvent("modified", str(skill_md)))

    handler.trigger("SKILL.md", changed_path=skill_md)

    for fmt in (AgentFormat.CLAUDE_CODE, AgentFormat.HERMES):
        copied = global_skill_root(fmt) / "fan-out" / "SKILL.md"
        assert "## From cursor" in copied.read_text(encoding="utf-8")


def test_skill_dir_containing_finds_the_skill_around_a_changed_file(home):
    root = global_skill_root(AgentFormat.CLAUDE_CODE)
    skill_dir = write_skill(root, "nested")
    nested = skill_dir / "references" / "deep" / "guide.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("# Guide\n", encoding="utf-8")

    assert watch.skill_dir_containing(nested, root) == skill_dir
    assert watch.skill_dir_containing(root / "loose.md", root) is None
    assert watch.skill_dir_containing(nested, global_skill_root(AgentFormat.HERMES)) is None
