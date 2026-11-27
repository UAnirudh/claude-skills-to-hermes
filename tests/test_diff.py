"""`diff`: what a sync would change, shown without writing anything."""

from __future__ import annotations

import shutil

from click.testing import CliRunner

from skill_bridge import cache
from skill_bridge.cli import main
from skill_bridge.diff import diff_profile, render
from skill_bridge.config import load_config
from skill_bridge.sync import run_sync
from tests.test_annotations import SKILL, write_project

MARKER = ".skill-bridge.json"


def diff(config, *args) -> object:
    return CliRunner().invoke(main, ["diff", "--config", str(config), *args])


def synced(tmp_path) -> tuple[object, object]:
    """A synced one-skill project: (config path, the copy's SKILL.md)."""
    config = write_project(tmp_path)
    run_sync(config_path=config)
    return config, tmp_path / "tgt" / "demo" / "SKILL.md"


def append(path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_a_clean_copy_has_nothing_to_diff(tmp_path):
    config, _ = synced(tmp_path)

    result = diff(config)

    assert result.exit_code == 0, result.output
    assert "1 copy(ies) checked, sync would change nothing." in result.output


def test_an_added_source_section_shows_up_as_added_lines(tmp_path):
    config, _ = synced(tmp_path)
    append(tmp_path / "src" / "demo" / "SKILL.md", "\n## Added later\n")

    result = diff(config)

    assert result.exit_code == 0, result.output
    assert "demo (hermes)" in result.output
    assert "--- a/SKILL.md" in result.output
    assert "+++ b/SKILL.md" in result.output
    assert "+## Added later" in result.output


def test_a_hand_edit_shows_the_text_a_sync_would_overwrite(tmp_path):
    config, copy = synced(tmp_path)
    append(copy, "\n<!-- mine -->\n")

    result = diff(config)

    assert result.exit_code == 0, result.output
    # The copy is the left side, so what the user wrote is what would go.
    assert "-<!-- mine -->" in result.output
    added = [
        line
        for line in result.output.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    assert added == []


def test_a_file_the_copy_lost_is_shown_as_created(tmp_path):
    config, _ = synced(tmp_path)
    (tmp_path / "src" / "demo" / "REFERENCE.md").write_text(
        "# Reference\n", encoding="utf-8"
    )

    result = diff(config)

    assert result.exit_code == 0, result.output
    assert "--- /dev/null" in result.output
    assert "+++ b/REFERENCE.md" in result.output
    assert "+# Reference" in result.output


def test_a_file_conversion_would_drop_is_shown_as_removed(tmp_path):
    config, copy = synced(tmp_path)
    (copy.parent / "NOTES.md").write_text("local notes\n", encoding="utf-8")

    result = diff(config)

    assert result.exit_code == 0, result.output
    assert "--- a/NOTES.md" in result.output
    assert "+++ /dev/null" in result.output
    assert "-local notes" in result.output


def test_the_summary_counts_files_and_copies(tmp_path):
    config, _ = synced(tmp_path)
    append(tmp_path / "src" / "demo" / "SKILL.md", "\n## Later\n")
    (tmp_path / "src" / "demo" / "REFERENCE.md").write_text("# Ref\n", encoding="utf-8")

    result = diff(config)

    assert result.exit_code == 0, result.output
    assert "2 file(s) in 1 copy(ies) would change." in result.output


def test_nothing_is_written(tmp_path):
    config, copy = synced(tmp_path)
    source = tmp_path / "src" / "demo" / "SKILL.md"
    append(source, "\n## Later\n")
    before = (copy.read_bytes(), source.read_bytes())
    marker = copy.parent / MARKER
    marker_before = marker.read_bytes()

    diff(config)

    assert (copy.read_bytes(), source.read_bytes()) == before
    assert marker.read_bytes() == marker_before


def test_the_marker_itself_is_never_part_of_the_diff(tmp_path):
    config, copy = synced(tmp_path)
    marker = copy.parent / MARKER
    marker.write_text(
        marker.read_text(encoding="utf-8").replace("1", "9", 1), encoding="utf-8"
    )

    result = diff(config)

    # The marker is bookkeeping, not content: a rewritten one is not a change
    # to the skill, so there is nothing to show.
    assert "sync would change nothing" in result.output
    assert MARKER not in result.output


def test_a_copy_whose_source_is_gone_is_left_out(tmp_path):
    config, _ = synced(tmp_path)
    shutil.rmtree(tmp_path / "src" / "demo")

    result = diff(config)

    # `sync --prune` is the answer for a stale copy, not a diff.
    assert result.exit_code == 0, result.output
    assert "1 copy(ies) checked, sync would change nothing." in result.output


def test_the_skill_option_limits_the_diff(tmp_path):
    config = write_project(tmp_path)
    other = tmp_path / "src" / "other"
    other.mkdir()
    (other / "SKILL.md").write_text(SKILL.replace("demo", "other"), encoding="utf-8")
    run_sync(config_path=config)
    append(tmp_path / "src" / "demo" / "SKILL.md", "\n## Later\n")

    quiet = diff(config, "--skill", "other")
    noisy = diff(config, "--skill", "demo")

    assert "sync would change nothing" in quiet.output
    assert "+## Later" in noisy.output


def test_a_skill_name_that_matches_no_copy_is_said_out_loud(tmp_path):
    config, _ = synced(tmp_path)

    result = diff(config, "--skill", "nope")

    assert result.exit_code == 1
    assert "no generated copy named 'nope'" in result.output
    # "nothing to change" would read as a clean profile rather than a typo.
    assert "sync would change nothing" not in result.output


def test_a_named_skill_that_is_clean_still_exits_zero(tmp_path):
    config, _ = synced(tmp_path)

    result = diff(config, "--skill", "demo")

    assert result.exit_code == 0, result.output
    assert "1 copy(ies) checked, sync would change nothing." in result.output


def test_a_file_without_a_trailing_newline_is_marked(tmp_path):
    config, copy = synced(tmp_path)
    copy.write_bytes(copy.read_bytes() + b"\n<!-- mine -->")

    result = diff(config)

    assert "-<!-- mine -->" in result.output
    # Without the marker the last line would silently gain a newline and the
    # patch would not apply.
    assert "\\ No newline at end of file" in result.output


def test_diff_text_is_not_read_as_markup(tmp_path):
    config, copy = synced(tmp_path)
    append(copy, "\n[dim]keep me[/dim]\n")

    result = diff(config)

    assert "-[dim]keep me[/dim]" in result.output


def test_an_unknown_profile_is_an_error(tmp_path):
    config, _ = synced(tmp_path)

    result = diff(config, "--profile", "nope")

    assert result.exit_code == 1
    assert "nope" in result.output


def test_a_second_diff_reads_the_conversion_back(tmp_path, monkeypatch):
    config, _ = synced(tmp_path)
    append(tmp_path / "src" / "demo" / "SKILL.md", "\n## Later\n")
    targets = load_config(config).get_profile("test").targets
    first = diff_profile(targets)

    def boom(*args, **kwargs):
        raise AssertionError("diff converted a source it had already converted")

    # `diff` shares doctor's cache, so looking at an unchanged profile again
    # must not convert anything.
    monkeypatch.setattr(cache, "convert_skill", boom)
    second = diff_profile(targets)

    assert second.diffs == first.diffs


def test_the_report_can_be_read_without_the_cli(tmp_path):
    config, _ = synced(tmp_path)
    append(tmp_path / "src" / "demo" / "SKILL.md", "\n## Later\n")

    report = diff_profile(load_config(config).get_profile("test").targets)

    assert report.checked == 1
    assert report.files == 1
    assert [diff.skill_name for diff in report.diffs] == ["demo"]
    assert [change.status for change in report.diffs[0].changes] == ["changed"]
    assert any(line.startswith("+## Later") for line in render(report))
