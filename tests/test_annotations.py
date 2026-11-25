"""The annotation writer: doctor's JSON report turned into workflow commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from skill_bridge.annotations import annotations as annotation_lines
from skill_bridge.annotations import escape_property
from skill_bridge.cli import main
from skill_bridge.sync import run_sync

SKILL = (
    "---\n"
    "name: demo\n"
    "description: Demo skill for the annotation tests.\n"
    "---\n\n"
    "# Demo\n\n"
    "Body paragraph.\n"
)

DETAIL = "source changed since this copy was converted"
COPY = "C:/t/.hermes/skills/demo/SKILL.md"


def write_project(tmp_path: Path) -> Path:
    """A one-skill project: generic source, hermes target, config file."""
    source = tmp_path / "src"
    (source / "demo").mkdir(parents=True)
    (source / "demo" / "SKILL.md").write_text(SKILL, encoding="utf-8")
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


def finding(
    kind: str = "out-of-sync",
    detail: str = DETAIL,
    *,
    files: list[dict] | None = None,
    file: str = COPY,
) -> dict:
    return {
        "kind": kind,
        "skill": "demo",
        "target": "hermes",
        "path": file.rsplit("/", 1)[0],
        "file": file,
        "detail": detail,
        "files": files or [],
    }


def report(*findings: dict) -> dict:
    return {
        "profile": "test",
        "checked": 1,
        "clean": not findings,
        "drift": 0,
        "counts": {},
        "findings": list(findings),
    }


def test_a_finding_points_at_the_line_that_differs():
    document = report(
        finding(
            files=[{"name": "SKILL.md", "status": "changed", "line": 7, "file": COPY}]
        )
    )

    assert annotation_lines(document) == [
        "::error file=C%3A/t/.hermes/skills/demo/SKILL.md,line=7::"
        "demo (hermes): source changed since this copy was converted"
    ]


def test_every_differing_file_gets_its_own_annotation():
    document = report(
        finding(
            files=[
                {"name": "SKILL.md", "status": "changed", "line": 4, "file": COPY},
                {
                    "name": "NOTES.md",
                    "status": "extra",
                    "line": 1,
                    "file": "C:/t/.hermes/skills/demo/NOTES.md",
                },
            ]
        )
    )

    lines = annotation_lines(document)

    assert [line.split("::")[1] for line in lines] == [
        "error file=C%3A/t/.hermes/skills/demo/SKILL.md,line=4",
        "error file=C%3A/t/.hermes/skills/demo/NOTES.md,line=1",
    ]
    # The message is the finding's, so a reader still knows what is wrong.
    assert all(line.endswith(f"::demo (hermes): {DETAIL}") for line in lines)


def test_a_finding_without_files_falls_back_to_the_copy_skill_md():
    document = report(finding("stale", "source directory was deleted or renamed"))

    assert annotation_lines(document) == [
        "::error file=C%3A/t/.hermes/skills/demo/SKILL.md,line=1::"
        "demo (hermes): source directory was deleted or renamed"
    ]


@pytest.mark.parametrize("kind", ["edited", "lossy"])
def test_a_finding_needing_a_decision_is_only_a_warning(kind):
    lines = annotation_lines(report(finding(kind, "model, effort")))

    assert len(lines) == 1
    assert lines[0].startswith(
        "::warning file=C%3A/t/.hermes/skills/demo/SKILL.md,line=1::"
    )
    # Commas are legal in a message, so the detail survives unchanged.
    assert lines[0].endswith("::demo (hermes): model, effort")


def test_newlines_percent_signs_and_path_separators_are_escaped():
    lines = annotation_lines(
        report(
            finding(
                "edited",
                "50% done\r\nsecond line",
                files=[
                    {
                        "name": "SKILL.md",
                        "status": "changed",
                        "line": 2,
                        "file": "C:/a,b:c/SKILL.md",
                    }
                ],
            )
        )
    )

    assert lines == [
        "::warning file=C%3A/a%2Cb%3Ac/SKILL.md,line=2::"
        "demo (hermes): 50%25 done%0D%0Asecond line"
    ]


def test_a_clean_report_emits_nothing():
    assert annotation_lines(report()) == []


def test_annotate_reads_a_report_from_a_file(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps(
            report(
                finding(
                    files=[
                        {"name": "SKILL.md", "status": "changed", "line": 9, "file": COPY}
                    ]
                )
            )
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["annotate", "--report", str(path)])

    assert result.exit_code == 0, result.output
    assert result.output.strip().endswith(",line=9::demo (hermes): " + DETAIL)


def test_annotate_reads_a_report_from_stdin():
    document = report(
        finding(
            files=[{"name": "SKILL.md", "status": "changed", "line": 3, "file": COPY}]
        )
    )

    result = CliRunner().invoke(
        main, ["annotate", "--report", "-"], input=json.dumps(document)
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip().startswith("::error file=C%3A/t/")


def test_annotate_refuses_something_that_is_not_a_report(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("this is not a report", encoding="utf-8")

    result = CliRunner().invoke(main, ["annotate", "--report", str(path)])

    assert result.exit_code == 1
    assert "not a doctor report" in result.output


def test_annotate_runs_the_check_itself(tmp_path):
    config = write_project(tmp_path)
    run_sync(config_path=config)
    source = tmp_path / "src" / "demo" / "SKILL.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n## Added later\n", encoding="utf-8"
    )

    result = CliRunner().invoke(main, ["annotate", "--config", str(config)])

    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("::error file=")
    # Naming the file is what keeps this off line 1.
    assert ",line=1::" not in lines[0]


def test_a_real_doctor_report_points_at_the_edited_line(tmp_path):
    config = write_project(tmp_path)
    run_sync(config_path=config)
    copy = tmp_path / "tgt" / "demo" / "SKILL.md"
    original = copy.read_text(encoding="utf-8").splitlines()
    original[-1] = "# rewritten by hand"
    copy.write_text("\n".join(original) + "\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["doctor", "--config", str(config), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    finding_payload = payload["findings"][0]
    assert finding_payload["kind"] == "edited"
    assert finding_payload["files"] == [
        {
            "name": "SKILL.md",
            "status": "changed",
            "line": len(original),
            "file": str(copy),
        }
    ]
    assert annotation_lines(payload) == [
        f"::warning file={escape_property(str(copy))},line={len(original)}::"
        "demo (hermes): edited by hand since it was written"
    ]
