"""The README harness table must match the registered exporters.

Keeps the docs honest: adding, renaming, or re-pathing an exporter without
updating the README table fails CI.
"""

from __future__ import annotations

import re
from pathlib import Path

from skill_bridge.formats.base import all_exporters, load_exporters

README = Path(__file__).resolve().parents[1] / "README.md"

ROW = re.compile(
    r"^\|\s*`(?P<id>[a-z0-9-]+)`\s*"
    r"\|\s*(?P<name>[^|]+?)\s*"
    r"\|\s*`(?P<project>[^`]+)`\s*"
    r"\|\s*`(?P<global>[^`]+)`\s*\|\s*$"
)


def readme_rows() -> dict[str, tuple[str, str, str]]:
    """Format ID -> (harness, project path, global path) from README.md."""
    rows: dict[str, tuple[str, str, str]] = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line.strip())
        if match:
            rows[match["id"]] = (
                match["name"].strip(),
                match["project"].strip(),
                match["global"].strip(),
            )
    return rows


def documented_global_path(exporter) -> str:
    subdir = exporter.global_skill_subdir or exporter.default_output_subdir
    return "~/" + subdir.replace("\\", "/")


def test_readme_table_lists_every_exporter():
    load_exporters()
    documented = readme_rows()
    exporters = {e.format_id.value: e for e in all_exporters()}

    assert set(documented) == set(exporters), (
        "README table and exporters disagree: "
        f"only in README={sorted(set(documented) - set(exporters))}, "
        f"only in code={sorted(set(exporters) - set(documented))}"
    )

    for format_id, exporter in exporters.items():
        _, project_path, global_path = documented[format_id]
        assert project_path == exporter.default_output_subdir.replace("\\", "/"), (
            f"README project path for '{format_id}' is stale"
        )
        assert global_path == documented_global_path(exporter), (
            f"README global path for '{format_id}' is stale"
        )
