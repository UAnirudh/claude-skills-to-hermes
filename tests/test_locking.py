"""One mutating run at a time: a second sync must not interleave with the first.

The lock is held by the operating system, so these tests use a real second
process rather than a nested call: within one process a nested run is the same
run and is meant to pass straight through.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from skill_bridge.cli import main
from skill_bridge.config import load_config
from skill_bridge.doctor import diagnose, repair
from skill_bridge.locking import LockTimeout, exclusive_run, lock_path
from skill_bridge.sync import run_prune, run_sync

SKILL = (
    "---\n"
    "name: {name}\n"
    "description: {name} for the locking tests.\n"
    "---\n\n"
    "# {name}\n\n"
    "Body paragraph.\n"
)

HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from skill_bridge.locking import exclusive_run

    with exclusive_run(Path(sys.argv[1]), wait=0):
        print("ready", flush=True)
        time.sleep(float(sys.argv[2]))
    """
)


def write_project(tmp_path: Path, *names: str) -> Path:
    source = tmp_path / "src"
    for name in names or ("demo",):
        (source / name).mkdir(parents=True)
        (source / name / "SKILL.md").write_text(
            SKILL.format(name=name), encoding="utf-8"
        )
    config = {
        "default_profile": "test",
        "profiles": {
            "test": {
                "source": {"format": "generic", "path": str(source)},
                "targets": [{"format": "hermes", "path": str(tmp_path / "tgt")}],
            }
        },
    }
    path = tmp_path / "skill-bridge.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def shell_output(result) -> str:
    """Whatever the command wrote, whether click kept it in one stream or two."""
    text = result.output
    try:
        text += result.stderr
    except (AttributeError, ValueError):  # older/newer click differences
        pass
    return text


def hold_lock(path: Path, seconds: float = 60.0) -> subprocess.Popen:
    """A separate process that holds ``path`` until it is told to stop."""
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path), str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    return process


def stop(process: subprocess.Popen, *, kill: bool = False) -> None:
    (process.kill if kill else process.terminate)()
    process.wait(timeout=30)
    if process.stdout is not None:
        process.stdout.close()


def test_the_lock_sits_beside_the_config(tmp_path):
    assert lock_path(tmp_path / "skill-bridge.yaml") == (
        tmp_path / ".skill-bridge.lock"
    ).resolve()


def test_a_nested_run_passes_straight_through(tmp_path):
    """doctor --fix prunes and then syncs while holding one lock."""
    path = tmp_path / ".skill-bridge.lock"

    with exclusive_run(path, wait=0):
        with exclusive_run(path, wait=0):
            acquired = True

    assert acquired


@pytest.mark.parametrize("action", ["sync", "prune", "repair"])
def test_every_mutating_run_gives_up_instead_of_sharing(tmp_path, action):
    config = write_project(tmp_path)
    holder = hold_lock(lock_path(config))
    try:
        with pytest.raises(LockTimeout) as caught:
            if action == "sync":
                run_sync(config_path=config, lock_wait=0)
            elif action == "prune":
                run_prune(config_path=config, lock_wait=0)
            else:
                report = diagnose(load_config(config).get_profile("test").targets)
                repair(report, config_path=config, lock_wait=0)
    finally:
        stop(holder)

    message = str(caught.value)
    assert ".skill-bridge.lock" in message
    assert "another skill-bridge run" in message


def test_a_killed_holder_leaves_no_stale_lock(tmp_path):
    config = write_project(tmp_path)
    path = lock_path(config)
    holder = hold_lock(path, seconds=300)
    stop(holder, kill=True)

    with exclusive_run(path, wait=2):
        pass  # the operating system released it with the process


def test_repair_holds_the_lock_across_prune_and_sync(tmp_path):
    config = write_project(tmp_path, "gone", "behind")
    run_sync(config_path=config)
    (tmp_path / "src" / "gone" / "SKILL.md").unlink()
    (tmp_path / "src" / "gone").rmdir()
    behind = tmp_path / "src" / "behind" / "SKILL.md"
    behind.write_text(
        behind.read_text(encoding="utf-8") + "\n## Added later\n", encoding="utf-8"
    )
    report = diagnose(load_config(config).get_profile("test").targets)

    result = repair(report, config_path=config, lock_wait=0)

    # Both steps re-entered the lock the repair already holds, so nothing timed
    # out and the whole repair happened under one guard.
    assert (result.pruned, result.refreshed) == (1, 1)
    assert not (tmp_path / "tgt" / "gone").exists()
    assert "## Added later" in (tmp_path / "tgt" / "behind" / "SKILL.md").read_text(
        encoding="utf-8"
    )


def test_the_command_names_the_lock_instead_of_crashing(tmp_path):
    config = write_project(tmp_path)
    holder = hold_lock(lock_path(config))
    try:
        result = CliRunner().invoke(
            main, ["sync", "--config", str(config), "--lock-timeout", "0"]
        )
    finally:
        stop(holder)

    assert result.exit_code == 1
    text = shell_output(result)
    assert "another skill-bridge run" in text
    assert "Traceback" not in text


def test_doctor_fix_names_the_lock_too(tmp_path):
    config = write_project(tmp_path)
    run_sync(config_path=config)
    # A repair only takes the lock when there is something to repair.
    (tmp_path / "src" / "demo" / "SKILL.md").unlink()
    (tmp_path / "src" / "demo").rmdir()
    holder = hold_lock(lock_path(config))
    try:
        result = CliRunner().invoke(
            main,
            [
                "doctor",
                "--config",
                str(config),
                "--fix",
                "--lock-timeout",
                "0",
            ],
        )
    finally:
        stop(holder)

    assert result.exit_code == 1
    assert "another skill-bridge run" in shell_output(result)


def test_a_waiting_run_continues_once_the_lock_is_free(tmp_path):
    config = write_project(tmp_path)
    path = lock_path(config)
    holder = hold_lock(path, seconds=0.4)
    started = time.monotonic()

    results = run_sync(config_path=config, lock_wait=30)

    stop(holder)
    assert [result.skill_name for result in results] == ["demo"]
    assert time.monotonic() - started < 30
    assert (tmp_path / "tgt" / "demo" / "SKILL.md").is_file()
