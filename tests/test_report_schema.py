"""The numbered report contract: `report-version` and `doctor --schema`.

A consumer that parses `doctor --json` is reading a format, not a debug dump.
These tests pin the promise: the version is a number in the document, the
schema describes exactly the document the command writes, and printing the
schema needs no profile.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from skill_bridge.cli import main
from skill_bridge.config import load_config
from skill_bridge.doctor import (
    CHANGED,
    EDITED,
    EXTRA,
    FileDifference,
    LOSSY,
    OUTDATED,
    OUT_OF_SYNC,
    REPORT_VERSION,
    STALE,
    DoctorReport,
    Finding,
    RepairResult,
    diagnose,
    report_schema,
    report_to_dict,
)
from skill_bridge.models import AgentFormat
from skill_bridge.sync import run_sync

SKILL = (
    "---\n"
    "name: demo\n"
    "description: Demo for the report schema tests.\n"
    "---\n\n"
    "# Demo\n\n"
    "Body paragraph.\n"
)

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Check a value against the subset of JSON Schema this schema uses.

    Small on purpose: pulling in a validator library to test our own schema
    would hide the part worth reading, which is whether the report still
    matches the document we publish.
    """
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not any(
            isinstance(value, _TYPES[name])
            and not (name != "boolean" and isinstance(value, bool))
            for name in names
        ):
            errors.append(f"{path}: {value!r} is not {expected}")
    minimum = schema.get("minimum")
    if minimum is not None and isinstance(value, int) and value < minimum:
        errors.append(f"{path}: {value} is below {minimum}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key!r}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                errors += validate(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected {key!r}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors += validate(item, schema["items"], f"{path}[{index}]")
    return errors


def write_project(tmp_path: Path) -> Path:
    source = tmp_path / "src" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(SKILL, encoding="utf-8")
    config = {
        "default_profile": "test",
        "profiles": {
            "test": {
                "source": {"format": "generic", "path": str(tmp_path / "src")},
                "targets": [{"format": "hermes", "path": str(tmp_path / "tgt")}],
            }
        },
    }
    path = tmp_path / "skill-bridge.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def full_report() -> DoctorReport:
    """One finding of every kind, with the file detail the report carries."""
    report = DoctorReport(checked=4)
    for kind, name in (
        (STALE, "gone"),
        (OUT_OF_SYNC, "stale"),
        (EDITED, "edited"),
        (LOSSY, "lossy"),
        (OUTDATED, "outdated"),
    ):
        report.findings.append(
            Finding(
                kind=kind,
                skill_name=name,
                target_format=AgentFormat.HERMES,
                skill_dir=Path("/tmp/tgt") / name,
                detail=f"{name} is {kind}",
                files=[
                    FileDifference("SKILL.md", CHANGED, 4),
                    FileDifference("notes.md", EXTRA, 1),
                ],
            )
        )
    return report


def test_the_schema_describes_what_the_report_writes():
    payload = report_to_dict(
        full_report(), profile="test", repair=RepairResult(1, 2, 3, 4, 5)
    )

    assert validate(payload, report_schema()) == []


def test_the_version_is_the_one_in_the_document():
    schema = report_schema()
    payload = report_to_dict(DoctorReport(), profile=None)

    assert payload["report-version"] == REPORT_VERSION
    assert schema["properties"]["report-version"]["const"] == REPORT_VERSION
    assert schema["$id"].endswith(f":{REPORT_VERSION}")
    assert validate(payload, schema) == []


def test_the_schema_names_every_finding_kind():
    kinds = report_schema()["properties"]["findings"]["items"]["properties"]["kind"]
    counts = report_schema()["properties"]["counts"]

    assert kinds["enum"] == [STALE, OUT_OF_SYNC, EDITED, LOSSY, OUTDATED]
    # `outdated` arrived after version 1 was published, so it is described but
    # not required: a document written before it still has to validate.
    assert counts["required"] == [STALE, OUT_OF_SYNC, EDITED, LOSSY]
    assert counts["properties"][OUTDATED] == {"type": "integer", "minimum": 0}


def test_schema_needs_no_profile_and_prints_only_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no skill-bridge.yaml anywhere
    result = CliRunner().invoke(main, ["doctor", "--schema"])

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["$schema"].startswith("https://json-schema.org/")
    assert document["properties"]["report-version"]["const"] == REPORT_VERSION


def test_a_real_report_validates_against_the_schema(tmp_path):
    config = write_project(tmp_path)
    run_sync(config_path=config)
    copy = tmp_path / "tgt" / "demo" / "SKILL.md"
    copy.write_text(
        copy.read_text(encoding="utf-8") + "\n## Written by hand\n", encoding="utf-8"
    )
    report = diagnose(load_config(config).get_profile("test").targets)
    assert [finding.kind for finding in report.findings] == [EDITED]

    payload = json.loads(
        CliRunner()
        .invoke(main, ["doctor", "--config", str(config), "--json"])
        .output
    )

    assert payload["report-version"] == REPORT_VERSION
    assert validate(payload, report_schema()) == []
    assert payload["findings"][0]["files"][0]["status"] == CHANGED


def test_fixed_is_only_present_when_something_was_repaired():
    schema = report_schema()

    assert "fixed" not in report_to_dict(DoctorReport(), profile="test")
    assert "fixed" in schema["properties"]


def test_annotate_tolerates_a_newer_report():
    """A consumer must not break on a version it has never seen."""
    document = {
        "report-version": REPORT_VERSION + 1,
        "profile": "test",
        "checked": 1,
        "clean": False,
        "drift": 0,
        "counts": {STALE: 0, OUT_OF_SYNC: 0, EDITED: 0, LOSSY: 1},
        "findings": [
            {
                "kind": LOSSY,
                "skill": "demo",
                "target": "hermes",
                "path": "/tmp/tgt/demo",
                "file": "/tmp/tgt/demo/SKILL.md",
                "detail": "frontmatter a target cannot express",
                "files": [],
                "added-in-a-later-version": True,
            }
        ],
    }

    result = CliRunner().invoke(
        main, ["annotate", "--report", "-"], input=json.dumps(document)
    )

    assert result.exit_code == 0, result.output
    assert "demo" in result.output
