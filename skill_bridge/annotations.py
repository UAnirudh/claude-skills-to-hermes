"""Turn a ``skill-bridge doctor --json`` report into GitHub annotations.

Kept in the package, not in a CI script, so a project that installs skill-bridge
from pip can annotate drift with ``skill-bridge annotate``. One annotation per
file a finding names, at the line that differs; a finding that cannot name a
file (a stale copy has none left to compare, a lossy one is about frontmatter)
falls back to the copy's ``SKILL.md`` at line 1.

Findings a command can fix (stale, out-of-sync) are errors; findings that need
a human decision (edited, lossy) are warnings, because ``--fix`` must not
overwrite either.
"""

from __future__ import annotations

#: Kinds a command fixes, and which therefore fail the annotation contract.
ERROR_KINDS = {"stale", "out-of-sync"}


def escape(value: str) -> str:
    """Escape an annotation message (workflow-command rules)."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    """Escape an annotation property: ``:`` and ``,`` matter inside ``key=``."""
    return escape(value).replace(":", "%3A").replace(",", "%2C")


def annotations(document: dict) -> list[str]:
    """One workflow command per file named by each finding, in report order."""
    lines: list[str] = []
    for finding in document.get("findings") or []:
        kind = str(finding.get("kind", "finding"))
        level = "error" if kind in ERROR_KINDS else "warning"
        # The skill and harness go in the message, not a title property: a
        # property value has to escape ``:`` and ``,``, which turns the one line
        # a reader sees into noise like ``doctor%3A out-of-sync``.
        who = f"{finding.get('skill', '?')} ({finding.get('target', '?')})"
        message = escape(f"{who}: {finding.get('detail') or kind}")
        targets = finding.get("files") or [
            {
                "file": finding.get("file") or finding.get("path") or "",
                "line": 1,
            }
        ]
        for item in targets:
            path = escape_property(str(item.get("file") or ""))
            line = int(item.get("line") or 1)
            lines.append(f"::{level} file={path},line={line}::{message}")
    return lines
