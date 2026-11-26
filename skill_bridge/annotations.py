"""Turn a ``skill-bridge doctor --json`` report into CI findings.

Two writers, one reading of the report: ``annotations`` prints GitHub workflow
commands for a CI log, and ``sarif`` builds a SARIF 2.1.0 log that GitHub code
scanning ingests, so drift shows up in the Security tab and on the pull request.

Both are kept in the package, not in a CI script, so a project that installs
skill-bridge from pip can publish findings with ``skill-bridge annotate``. One
finding per file a finding names, at the line that differs; a finding that
cannot name a file (a stale copy has none left to compare, a lossy one is about
frontmatter) falls back to the copy's ``SKILL.md`` at line 1.

Findings a command can fix (stale, out-of-sync) are errors; findings that need
a human decision (edited, lossy) are warnings, because ``--fix`` must not
overwrite either. Both writers use the same rule, so a project's CI log and its
code-scanning alerts cannot disagree about what is urgent.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

#: Kinds a command fixes, and which therefore fail the annotation contract.
ERROR_KINDS = {"stale", "out-of-sync"}

#: The SARIF version GitHub code scanning ingests, and its published schema.
SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

#: The tool a code-scanning alert is attributed to.
TOOL_NAME = "skill-bridge"
TOOL_URI = "https://github.com/UAnirudh/claude-skills-to-hermes"

#: One rule per kind, so alerts group by problem instead of by file. A kind
#: this version does not know falls back to the kind itself, so a report from a
#: newer skill-bridge still produces a valid log.
RULE_TITLES = {
    "stale": "copy of a skill whose source is gone",
    "out-of-sync": "copy of a skill that is behind its source",
    "edited": "copy of a skill that was edited by hand",
    "lossy": "copy of a skill that lost fields in conversion",
    "outdated": "copy of a skill converted by an older skill-bridge",
}


def escape(value: str) -> str:
    """Escape an annotation message (workflow-command rules)."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    """Escape an annotation property: ``:`` and ``,`` matter inside ``key=``."""
    return escape(value).replace(":", "%3A").replace(",", "%2C")


def level_for(kind: str) -> str:
    """The level a kind carries, in both writers: errors are what a command fixes."""
    return "error" if kind in ERROR_KINDS else "warning"


def _message(finding: dict) -> str:
    """The one line a reader sees for a finding, before any escaping."""
    kind = str(finding.get("kind", "finding"))
    who = f"{finding.get('skill', '?')} ({finding.get('target', '?')})"
    return f"{who}: {finding.get('detail') or kind}"


def _targets(finding: dict) -> list[tuple[str, int]]:
    """The files a finding names, with a usable line number for each."""
    items = finding.get("files") or [
        {"file": finding.get("file") or finding.get("path") or "", "line": 1}
    ]
    targets: list[tuple[str, int]] = []
    for item in items:
        try:
            line = max(1, int(item.get("line") or 1))
        except (TypeError, ValueError):
            line = 1
        targets.append((str(item.get("file") or ""), line))
    return targets


def annotations(document: dict) -> list[str]:
    """One workflow command per file named by each finding, in report order."""
    lines: list[str] = []
    for finding in document.get("findings") or []:
        kind = str(finding.get("kind", "finding"))
        # The skill and harness go in the message, not a title property: a
        # property value has to escape ``:`` and ``,``, which turns the one line
        # a reader sees into noise like ``doctor%3A out-of-sync``.
        message = escape(_message(finding))
        for path, line in _targets(finding):
            lines.append(
                f"::{level_for(kind)} file={escape_property(path)},line={line}::{message}"
            )
    return lines


def _uri(path: str, root: Path) -> str:
    """A URI for a report path, relative to ``root`` when it is inside it.

    Code scanning matches a result to a file by its path in the repository, so a
    report produced inside the checkout should give relative paths; one from
    elsewhere (a run against a global skills directory) keeps its absolute path
    rather than pointing at an unrelated file.
    """
    if not path:
        return ""
    normalized = path
    try:
        normalized = Path(path).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        pass
    return quote(normalized.replace("\\", "/"), safe="/:")


def sarif(document: dict, *, root: Path | None = None) -> dict:
    """The report as a SARIF 2.1.0 log, for GitHub code scanning.

    One rule per kind, so alerts group by problem, and one result per file a
    finding names, at the line that differs. ``root`` (the working directory by
    default) is what a path is made relative to. A clean report is still a valid
    log, with no results, so a workflow can always upload it.
    """
    base = (Path(root) if root is not None else Path.cwd()).resolve()
    rules: list[dict] = []
    rule_indexes: dict[str, int] = {}
    results: list[dict] = []

    for finding in document.get("findings") or []:
        kind = str(finding.get("kind", "finding"))
        rule_id = f"{TOOL_NAME}/{kind}"
        if rule_id not in rule_indexes:
            rule_indexes[rule_id] = len(rules)
            rules.append(
                {
                    "id": rule_id,
                    "name": kind.replace("-", " ").title().replace(" ", ""),
                    "shortDescription": {"text": RULE_TITLES.get(kind, kind)},
                    "defaultConfiguration": {"level": level_for(kind)},
                }
            )
        message = _message(finding)
        for path, line in _targets(finding):
            uri = _uri(path, base)
            # The fingerprint names the problem and the file, never the line, so
            # an alert survives an edit that only moves it down the file.
            fingerprint = hashlib.sha256(
                f"{rule_id}|{uri}".encode("utf-8")
            ).hexdigest()
            results.append(
                {
                    "ruleId": rule_id,
                    "ruleIndex": rule_indexes[rule_id],
                    "level": level_for(kind),
                    "message": {"text": message},
                    "locations": (
                        [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": uri},
                                    "region": {"startLine": line},
                                }
                            }
                        ]
                        if uri
                        else []
                    ),
                    "partialFingerprints": {"skillBridge/v1": fingerprint},
                }
            )

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "informationUri": TOOL_URI,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
