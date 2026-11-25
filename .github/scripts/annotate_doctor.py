#!/usr/bin/env python3
"""Turn a ``skill-bridge doctor --json`` report into GitHub annotations.

Reads the document on stdin and prints one workflow command per finding, so
drift appears on the copy it belongs to instead of only failing the job. A
finding a command can fix (a stale or out-of-sync copy) is an error; a finding
that needs a human decision (an edited or lossy copy) is a warning, because
``--fix`` must never overwrite one.

Standard library only, and no import of ``skill_bridge``: CI runs this against
whatever the checked-out project produced, including from a plain interpreter.
"""

from __future__ import annotations

import json
import sys

#: Kinds a command fixes, and which therefore fail the annotation contract.
ERROR_KINDS = {"stale", "out-of-sync"}


def escape(value: str) -> str:
    """Escape an annotation message (workflow-command rules)."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    """Escape an annotation property: ``:`` and ``,`` matter inside ``key=``."""
    return escape(value).replace(":", "%3A").replace(",", "%2C")


def annotations(document: dict) -> list[str]:
    """One annotation per finding, in the order doctor reported them."""
    lines: list[str] = []
    for finding in document.get("findings") or []:
        kind = str(finding.get("kind", "finding"))
        level = "error" if kind in ERROR_KINDS else "warning"
        path = escape_property(str(finding.get("file") or finding.get("path") or ""))
        # The skill and harness go in the message, not a title property: a
        # property value has to escape ``:`` and ``,``, which turns the one
        # line a reader sees into noise like ``doctor%3A out-of-sync``.
        who = f"{finding.get('skill', '?')} ({finding.get('target', '?')})"
        message = escape(f"{who}: {finding.get('detail') or kind}")
        # Line 1: a finding is about the copy as a whole, and SKILL.md is the
        # file a reviewer recognises.
        lines.append(f"::{level} file={path},line=1::{message}")
    return lines


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("annotate_doctor: no doctor report on stdin", file=sys.stderr)
        return 2
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"annotate_doctor: not a doctor report: {exc}", file=sys.stderr)
        return 2
    for line in annotations(document):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
