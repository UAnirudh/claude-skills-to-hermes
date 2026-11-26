"""The SARIF writer: doctor's JSON report as a code-scanning log."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from skill_bridge.annotations import (
    ERROR_KINDS,
    SARIF_SCHEMA,
    SARIF_VERSION,
    sarif,
)
from skill_bridge.cli import main
from skill_bridge.sync import run_sync
from tests.test_annotations import COPY, DETAIL, finding, report, write_project

LEVELS = {
    "stale": "error",
    "out-of-sync": "error",
    "edited": "warning",
    "lossy": "warning",
    "outdated": "warning",
}


def run_of(document: dict) -> dict:
    log = sarif(document)
    assert log["runs"], "a log always carries one run"
    return log["runs"][0]


def only_result(document: dict) -> dict:
    results = run_of(document)["results"]
    assert len(results) == 1, results
    return results[0]


def location_of(result: dict) -> tuple[str, int]:
    physical = result["locations"][0]["physicalLocation"]
    return (
        physical["artifactLocation"]["uri"],
        physical["region"]["startLine"],
    )


def test_a_report_becomes_a_sarif_log():
    log = sarif(report(finding()))

    assert log["$schema"] == SARIF_SCHEMA
    assert log["version"] == SARIF_VERSION == "2.1.0"
    assert len(log["runs"]) == 1

    driver = log["runs"][0]["tool"]["driver"]
    assert driver["name"] == "skill-bridge"
    assert driver["informationUri"].startswith("https://")
    assert [rule["id"] for rule in driver["rules"]] == ["skill-bridge/out-of-sync"]


@pytest.mark.parametrize("kind,level", sorted(LEVELS.items()))
def test_each_kind_gets_its_level_and_its_rule(kind: str, level: str):
    run = run_of(report(finding(kind)))

    result = run["results"][0]
    assert result["ruleId"] == f"skill-bridge/{kind}"
    assert result["level"] == level
    # The rule a result points at is the one that carries its default level, so
    # a consumer that only reads the rule list still gets the severity right.
    rule = run["tool"]["driver"]["rules"][result["ruleIndex"]]
    assert rule["id"] == result["ruleId"]
    assert rule["defaultConfiguration"]["level"] == level
    assert rule["shortDescription"]["text"]
    assert (kind in ERROR_KINDS) == (level == "error")


def test_every_differing_file_becomes_its_own_result():
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

    run = run_of(document)

    assert [location_of(result) for result in run["results"]] == [
        ("C:/t/.hermes/skills/demo/SKILL.md", 4),
        ("C:/t/.hermes/skills/demo/NOTES.md", 1),
    ]
    # One rule, so code scanning groups the two alerts instead of splitting them.
    assert len(run["tool"]["driver"]["rules"]) == 1
    assert all(result["ruleIndex"] == 0 for result in run["results"])


def test_a_finding_without_files_falls_back_to_the_copy_skill_md():
    result = only_result(report(finding("stale", "source directory was deleted")))

    assert location_of(result) == (COPY, 1)
    assert result["message"]["text"] == (
        "demo (hermes): source directory was deleted"
    )


def test_a_line_is_never_below_one():
    result = only_result(
        report(
            finding(
                files=[{"name": "SKILL.md", "status": "changed", "line": 0, "file": COPY}]
            )
        )
    )

    assert location_of(result)[1] == 1


def test_backslashes_become_forward_slashes():
    windows = r"C:\t\.hermes\skills\demo\SKILL.md"

    assert location_of(only_result(report(finding(file=windows))))[0] == COPY


def test_a_path_inside_the_working_directory_is_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    copy = tmp_path / ".hermes" / "skills" / "demo" / "SKILL.md"
    copy.parent.mkdir(parents=True)
    copy.write_text("# Demo\n", encoding="utf-8")

    result = only_result(report(finding(file=str(copy))))

    assert location_of(result)[0] == ".hermes/skills/demo/SKILL.md"


def test_a_path_outside_the_working_directory_stays_absolute(tmp_path):
    elsewhere = tmp_path / "global" / "SKILL.md"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("# Demo\n", encoding="utf-8")

    result = only_result(report(finding(file=str(elsewhere))))

    assert location_of(result)[0] == elsewhere.as_posix()
    assert not location_of(result)[0].startswith(".")


def test_the_fingerprint_follows_the_file_not_the_line():
    def at(line: int) -> dict:
        return only_result(
            report(
                finding(
                    files=[
                        {"name": "SKILL.md", "status": "changed", "line": line, "file": COPY}
                    ]
                )
            )
        )

    assert at(4)["partialFingerprints"] == at(9)["partialFingerprints"]
    assert location_of(at(4))[1] != location_of(at(9))[1]


def test_a_different_file_is_a_different_alert():
    def at(path: str) -> dict:
        return only_result(report(finding(file=path)))

    assert (
        at(COPY)["partialFingerprints"] != at("C:/t/other/SKILL.md")["partialFingerprints"]
    )


def test_a_message_is_plain_text_not_a_workflow_command():
    result = only_result(report(finding("edited", "50% done\nsecond line")))

    # SARIF carries JSON, so the workflow-command escaping would be visible
    # noise in the Security tab.
    assert result["message"]["text"] == "demo (hermes): 50% done\nsecond line"


def test_a_clean_report_is_an_empty_but_valid_log():
    run = run_of(report())

    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []
    assert run["tool"]["driver"]["name"] == "skill-bridge"


def test_an_unknown_kind_is_still_a_valid_result():
    result = only_result(report(finding("future-kind", "something new")))

    assert result["ruleId"] == "skill-bridge/future-kind"
    assert result["level"] == "warning"
    assert result["message"]["text"] == "demo (hermes): something new"


def test_the_cli_writes_sarif_from_a_file(tmp_path):
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

    result = CliRunner().invoke(
        main, ["annotate", "--report", str(path), "--format", "sarif"]
    )

    assert result.exit_code == 0, result.output
    log = json.loads(result.output)
    assert log["version"] == "2.1.0"
    assert location_of(log["runs"][0]["results"][0]) == (COPY, 9)
    assert log["runs"][0]["results"][0]["message"]["text"] == (
        f"demo (hermes): {DETAIL}"
    )


def test_the_cli_writes_sarif_from_stdin():
    result = CliRunner().invoke(
        main,
        ["annotate", "--report", "-", "--format", "sarif"],
        input=json.dumps(report(finding())),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["runs"][0]["results"][0]["ruleId"] == (
        "skill-bridge/out-of-sync"
    )


def test_the_default_format_is_still_the_github_one(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report(finding())), encoding="utf-8")

    result = CliRunner().invoke(main, ["annotate", "--report", str(path)])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("::error file=")


def test_an_unknown_format_is_refused(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report(finding())), encoding="utf-8")

    result = CliRunner().invoke(
        main, ["annotate", "--report", str(path), "--format", "junit"]
    )

    assert result.exit_code != 0
    assert "junit" in result.output


def test_a_real_doctor_report_becomes_sarif(tmp_path):
    config = write_project(tmp_path)
    run_sync(config_path=config)
    source = tmp_path / "src" / "demo" / "SKILL.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n## Added later\n", encoding="utf-8"
    )

    result = CliRunner().invoke(
        main, ["annotate", "--config", str(config), "--format", "sarif"]
    )

    assert result.exit_code == 0, result.output
    run = json.loads(result.output)["runs"][0]
    assert len(run["results"]) == 1
    assert run["results"][0]["ruleId"] == "skill-bridge/out-of-sync"
    # The report names the line that differs, so the alert is not on line 1.
    assert location_of(run["results"][0])[1] > 1
