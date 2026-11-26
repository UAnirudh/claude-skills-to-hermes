"""``accept``: bless a hand edit instead of warning about it forever.

`doctor` will not overwrite a copy whose contents it did not write, which leaves
the user two ways out: throw the edit away with `sync`, or keep it and stop the
warnings. This is the second one.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from skill_bridge.cli import main
from skill_bridge.config import load_config
from skill_bridge.doctor import EDITED, LOSSY, diagnose
from skill_bridge.locking import lock_path
from skill_bridge.provenance import read_marker, tree_hash
from skill_bridge.sync import run_sync

SKILL = (
    "---\n"
    "name: {name}\n"
    "description: {name} for the accept tests.\n"
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
        time.sleep(60)
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


def write_two_targets(tmp_path: Path, *names: str) -> Path:
    """One profile, two harnesses, so a copy exists in each."""
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
                "targets": [
                    {"format": "hermes", "path": str(tmp_path / "hermes")},
                    {"format": "claude-code", "path": str(tmp_path / "claude")},
                ],
            }
        },
    }
    path = tmp_path / "skill-bridge.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def targets_of(config: Path):
    return load_config(config).get_profile("test").targets


def edit_copy(tmp_path: Path, name: str = "demo") -> Path:
    copy = tmp_path / "tgt" / name / "SKILL.md"
    copy.write_text(
        copy.read_text(encoding="utf-8") + "\n## Written by hand\n", encoding="utf-8"
    )
    return copy


def hold_lock(path: Path) -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    return process


def stop(process: subprocess.Popen) -> None:
    process.terminate()
    process.wait(timeout=30)
    if process.stdout is not None:
        process.stdout.close()


def output_of(result) -> str:
    text = result.output
    try:
        text += result.stderr
    except (AttributeError, ValueError):
        pass
    return text


def test_accepting_an_edit_clears_the_warning(tmp_path):
    config = write_project(tmp_path)
    run_sync(config_path=config)
    copy = edit_copy(tmp_path)
    before = read_marker(copy.parent)
    assert [finding.kind for finding in diagnose(targets_of(config)).findings] == [
        EDITED
    ]

    result = CliRunner().invoke(main, ["accept", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "Accepted 1 copy" in result.output
    marker = read_marker(copy.parent)
    assert marker["output-sha"] == tree_hash(copy.parent)
    assert marker["local-changes"] is True
    assert marker["accepted-at"]
    # Blessing the edit keeps everything the marker already knew about it.
    assert marker["source-path"] == before["source-path"]
    assert marker["source-rel"] == before["source-rel"]
    assert "## Written by hand" in copy.read_text(encoding="utf-8")
    follow = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--strict"]
    )
    assert follow.exit_code == 0, follow.output


def test_a_later_source_change_is_reported_not_overwritten(tmp_path):
    config = write_project(tmp_path)
    run_sync(config_path=config)
    copy = edit_copy(tmp_path)
    CliRunner().invoke(main, ["accept", "--config", str(config)])
    source = tmp_path / "src" / "demo" / "SKILL.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n## Added later\n", encoding="utf-8"
    )

    report = diagnose(targets_of(config))

    assert [finding.kind for finding in report.findings] == [EDITED]
    assert report.findings[0].detail == "accepted copy, and its source changed since"
    fix = CliRunner().invoke(
        main, ["doctor", "--config", str(config), "--fix", "--strict"]
    )
    assert fix.exit_code == 1  # still drift, and still a decision to make
    assert "## Written by hand" in copy.read_text(encoding="utf-8")
    # An explicit sync is still how the source's version wins.
    run_sync(config_path=config)
    assert "## Added later" in copy.read_text(encoding="utf-8")


def test_accepting_blesses_edits_in_every_target(tmp_path):
    """One profile can have several targets; each copy gets its own marker."""
    config = write_two_targets(tmp_path)
    run_sync(config_path=config)
    copies = [
        tmp_path / "hermes" / "demo" / "SKILL.md",
        tmp_path / "claude" / "demo" / "SKILL.md",
    ]
    for copy in copies:
        copy.write_text(
            copy.read_text(encoding="utf-8") + "\n## Written by hand\n",
            encoding="utf-8",
        )
    assert [finding.kind for finding in diagnose(targets_of(config)).findings] == [
        EDITED,
        EDITED,
    ]

    result = CliRunner().invoke(main, ["accept", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "Accepted 2 copy(ies)" in result.output
    assert diagnose(targets_of(config)).findings == []
    for copy in copies:
        assert read_marker(copy.parent)["output-sha"] == tree_hash(copy.parent)
        assert "## Written by hand" in copy.read_text(encoding="utf-8")
    # Accepting one target does not reach into the other's marker.
    assert read_marker(copies[0].parent)["target-format"] == "hermes"
    assert read_marker(copies[1].parent)["target-format"] == "claude-code"


def test_accepting_nothing_says_so(tmp_path):
    config = write_project(tmp_path)
    run_sync(config_path=config)

    result = CliRunner().invoke(main, ["accept", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "Nothing to accept" in result.output


def test_one_skill_can_be_accepted_alone(tmp_path):
    config = write_project(tmp_path, "one", "two")
    run_sync(config_path=config)
    edit_copy(tmp_path, "one")
    edit_copy(tmp_path, "two")

    result = CliRunner().invoke(
        main, ["accept", "--config", str(config), "--skill", "one"]
    )

    assert result.exit_code == 0, result.output
    assert {finding.skill_name: finding.kind for finding in diagnose(
        targets_of(config)
    ).findings} == {"two": EDITED}


def test_a_lossy_copy_is_not_something_to_accept(tmp_path):
    """Lossy is a property of the format pair, not an edit to bless."""
    config = write_project(tmp_path)
    source = tmp_path / "src" / "demo" / "SKILL.md"
    source.write_text(
        "---\n"
        "name: demo\n"
        "description: Demo with frontmatter hermes cannot express.\n"
        "model: opus\n"
        "---\n\n"
        "# Demo\n\n"
        "Body paragraph.\n",
        encoding="utf-8",
    )
    run_sync(config_path=config)

    result = CliRunner().invoke(main, ["accept", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "Nothing to accept" in result.output
    assert [finding.kind for finding in diagnose(targets_of(config)).findings] == [
        LOSSY
    ]


def test_accept_takes_the_run_lock(tmp_path):
    config = write_project(tmp_path)
    run_sync(config_path=config)
    edit_copy(tmp_path)
    holder = hold_lock(lock_path(config))
    try:
        result = CliRunner().invoke(
            main, ["accept", "--config", str(config), "--lock-timeout", "0"]
        )
    finally:
        stop(holder)

    assert result.exit_code == 1
    assert "another skill-bridge run" in output_of(result)


def test_accepting_a_copy_without_fingerprints_makes_it_healthy(tmp_path):
    """Markers from older versions carry no hashes; accepting one adds them."""
    config = write_project(tmp_path)
    run_sync(config_path=config)
    copy = edit_copy(tmp_path)
    marker = read_marker(copy.parent)
    marker.pop("output-sha")
    marker.pop("source-sha")
    (copy.parent / ".skill-bridge.json").write_text(
        json.dumps(marker, indent=2) + "\n", encoding="utf-8"
    )

    result = CliRunner().invoke(main, ["accept", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "Accepted 1 copy" in result.output
    assert read_marker(copy.parent)["output-sha"] == tree_hash(copy.parent)
    assert [finding.kind for finding in diagnose(targets_of(config)).findings] == []
