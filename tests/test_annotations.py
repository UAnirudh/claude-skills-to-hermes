"""The CI annotator: doctor's JSON report turned into workflow commands."""

from __future__ import annotations

import io
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from skill_bridge.config import SkillLocation, SyncProfile
from skill_bridge.doctor import diagnose, report_to_dict
from skill_bridge.models import AgentFormat
from skill_bridge.sync import sync_profile

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / (
    "annotate_doctor.py"
)

SKILL_MD = "---\nname: demo\ndescription: Demo.\n---\n\n# Demo\n"


def load_script():
    spec = spec_from_file_location("annotate_doctor", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


annotate = load_script()


def finding(kind, detail, *, file="C:/t/.hermes/skills/demo/SKILL.md"):
    return {
        "kind": kind,
        "skill": "demo",
        "target": "hermes",
        "path": file.rsplit("/", 1)[0],
        "file": file,
        "detail": detail,
    }


def report(*findings):
    return {
        "profile": "test",
        "checked": 1,
        "clean": not findings,
        "drift": 0,
        "counts": {},
        "findings": list(findings),
    }


def test_drift_becomes_an_error_on_the_skill_md():
    lines = annotate.annotations(
        report(finding("out-of-sync", "source changed since this copy was converted"))
    )

    # ``:`` is escaped in a property value; GitHub unescapes it, so the
    # annotation still points at the Windows path.
    assert lines == [
        "::error file=C%3A/t/.hermes/skills/demo/SKILL.md,line=1::"
        "demo (hermes): source changed since this copy was converted"
    ]


@pytest.mark.parametrize("kind", ["edited", "lossy"])
def test_a_finding_needing_a_decision_is_only_a_warning(kind):
    lines = annotate.annotations(report(finding(kind, "model, effort")))

    assert len(lines) == 1
    assert lines[0].startswith(
        "::warning file=C%3A/t/.hermes/skills/demo/SKILL.md,line=1::"
    )
    # Commas are legal in a message, so the detail survives unchanged.
    assert lines[0].endswith("::demo (hermes): model, effort")


def test_one_finding_per_copy_in_report_order():
    lines = annotate.annotations(
        report(
            finding("stale", "source directory was deleted or renamed"),
            finding("lossy", "model", file="C:/t/.gemini/skills/demo/SKILL.md"),
        )
    )

    assert [line.split("::")[1] for line in lines] == [
        "error file=C%3A/t/.hermes/skills/demo/SKILL.md,line=1",
        "warning file=C%3A/t/.gemini/skills/demo/SKILL.md,line=1",
    ]


def test_newlines_percent_signs_and_path_separators_are_escaped():
    lines = annotate.annotations(
        report(
            finding(
                "edited",
                "50% done\r\nsecond line",
                file="C:/a,b:c/SKILL.md",
            )
        )
    )

    assert lines == [
        "::warning file=C%3A/a%2Cb%3Ac/SKILL.md,line=1::"
        "demo (hermes): 50%25 done%0D%0Asecond line"
    ]


def test_a_clean_report_emits_nothing():
    assert annotate.annotations(report()) == []


def test_main_reads_the_document_from_stdin(monkeypatch, capsys):
    document = report(finding("stale", "source directory was deleted or renamed"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(document)))

    assert annotate.main() == 0

    out = capsys.readouterr().out
    assert out.count("::error file=") == 1
    assert "source directory was deleted or renamed" in out


@pytest.mark.parametrize("raw", ["", "   \n", "not json"])
def test_main_refuses_input_that_is_not_a_report(monkeypatch, capsys, raw):
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))

    assert annotate.main() == 2

    assert "annotate_doctor:" in capsys.readouterr().err


def test_the_annotation_matches_what_doctor_actually_reports(tmp_path):
    source = tmp_path / "src" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    target = tmp_path / "tgt"
    profile = SyncProfile(
        name="test",
        source=SkillLocation(format=AgentFormat.GENERIC, path=source),
        targets=[SkillLocation(format=AgentFormat.HERMES, path=target)],
    )
    sync_profile(profile)
    (source / "SKILL.md").write_text(SKILL_MD + "\n## Later\n", encoding="utf-8")

    payload = report_to_dict(diagnose(profile.targets), profile="test")

    # The path goes through the property escaping, which matters on Windows:
    # ``C:\...`` becomes ``C%3A\...`` before GitHub sees it.
    escaped = annotate.escape_property(str(target / "demo" / "SKILL.md"))
    assert annotate.annotations(payload) == [
        f"::error file={escaped},line=1::"
        "demo (hermes): source changed since this copy was converted"
    ]
