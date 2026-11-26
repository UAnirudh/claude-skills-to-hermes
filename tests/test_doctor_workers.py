"""doctor re-converts drifted copies together, and reports the same either way.

Each conversion is the expensive half of a check and needs nothing from the
others, so a profile with many drifted copies should not pay for them one after
another. What must not change is the report: same findings, same order, and a
clean profile still converts nothing at all.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from skill_bridge import doctor as doctor_module
from skill_bridge.config import load_config
from skill_bridge.doctor import DEFAULT_WORKERS, _worker_count, diagnose
from skill_bridge.sync import run_sync
from tests.test_annotations import SKILL, write_project

NAMES = ("demo", "bravo", "charlie")


def three_drifted(tmp_path: Path) -> Path:
    """A profile with three skills, each behind its source."""
    config = write_project(tmp_path)
    for name in NAMES[1:]:
        folder = tmp_path / "src" / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(SKILL.replace("demo", name), encoding="utf-8")
    run_sync(config_path=config)
    for name in NAMES:
        path = tmp_path / "src" / name / "SKILL.md"
        path.write_text(
            path.read_text(encoding="utf-8") + f"\n## {name} later\n", encoding="utf-8"
        )
    return config


def targets(config: Path):
    return load_config(config).get_profile("test").targets


def peak_concurrency(monkeypatch, config: Path, workers: int | None) -> int:
    """Run the check, counting how many conversions were in flight at once."""
    real = doctor_module.convert_skill
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def slow(*args, **kwargs):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            # Long enough that a serial run cannot accidentally overlap.
            time.sleep(0.05)
            return real(*args, **kwargs)
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(doctor_module, "convert_skill", slow)
    diagnose(targets(config), workers=workers)
    return state["peak"]


def test_several_drifted_copies_are_converted_at_once(tmp_path, monkeypatch):
    config = three_drifted(tmp_path)

    assert peak_concurrency(monkeypatch, config, workers=3) >= 2


def test_one_worker_stays_serial(tmp_path, monkeypatch):
    config = three_drifted(tmp_path)

    assert peak_concurrency(monkeypatch, config, workers=1) == 1


def test_the_report_is_the_same_whichever_way_it_ran(tmp_path):
    config = three_drifted(tmp_path)

    serial = diagnose(targets(config), workers=1)
    parallel = diagnose(targets(config), workers=3)

    def view(report):
        return [
            (finding.kind, finding.skill_name, finding.detail, finding.files)
            for finding in report.findings
        ]

    assert view(serial) == view(parallel)
    assert serial.checked == parallel.checked == 3


def test_every_drifted_copy_is_reported_once(tmp_path):
    config = three_drifted(tmp_path)

    report = diagnose(targets(config), workers=3)

    assert sorted(finding.skill_name for finding in report.findings) == sorted(NAMES)
    assert [finding.kind for finding in report.findings] == ["out-of-sync"] * 3


def test_a_clean_profile_converts_nothing(tmp_path, monkeypatch):
    config = write_project(tmp_path)
    run_sync(config_path=config)

    def refuse(*args, **kwargs):
        raise AssertionError("a copy that matches its source was re-converted")

    monkeypatch.setattr(doctor_module, "convert_skill", refuse)

    assert diagnose(targets(config)).findings == []


@pytest.mark.parametrize(
    "workers,jobs,expected",
    [
        (None, 1, 1),
        (None, 100, DEFAULT_WORKERS),
        (8, 3, 3),
        (1, 3, 1),
        (0, 3, 1),
    ],
)
def test_workers_are_bounded_by_the_work_and_never_zero(workers, jobs, expected):
    assert _worker_count(workers, jobs) == expected
