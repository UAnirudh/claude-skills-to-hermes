"""`doctor --all-profiles`: one pass over every profile in the config.

Profiles in one config often name the same folder — `install` writes an
`auto-import` profile beside the user's own, and both can target one harness —
so the check is shared: a copy is reported under the first profile that names
it and counted once. The JSON document keeps its shape and gains a per-profile
summary.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from skill_bridge.cli import main
from skill_bridge.config import load_config
from skill_bridge.doctor import diagnose_profiles
from skill_bridge.sync import run_sync

NAMES = ("demo", "extra")


def write_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name} used by the all-profiles tests.\n"
        "---\n\n"
        f"# {name}\n\n"
        "Body paragraph.\n",
        encoding="utf-8",
    )
    return skill_dir


def write_config(tmp_path: Path, profiles: dict[str, list[str]]) -> Path:
    """A config whose profiles all read one source tree.

    ``profiles`` maps a profile name to the harnesses it writes to, so two
    profiles can be given the same harness on purpose.
    """
    source = tmp_path / "src"
    for name in NAMES:
        write_skill(source, name)
    data = {"default_profile": next(iter(profiles)), "profiles": {}}
    for name, targets in profiles.items():
        data["profiles"][name] = {
            "source": {"format": "generic", "path": str(source)},
            "targets": [
                {"format": fmt, "path": str(tmp_path / f".{fmt}" / "skills")}
                for fmt in targets
            ],
        }
    path = tmp_path / "skill-bridge.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def sync_all(config: Path) -> None:
    for name in load_config(config).profiles:
        run_sync(profile_name=name, config_path=config)


def drift_sources(tmp_path: Path) -> None:
    for name in NAMES:
        path = tmp_path / "src" / name / "SKILL.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "\n## Added later\n", encoding="utf-8"
        )


def invoke(*args: str):
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


def test_each_profile_is_checked_separately(tmp_path):
    config = write_config(tmp_path, {"hermes": ["hermes"], "cursor": ["cursor"]})
    sync_all(config)
    drift_sources(tmp_path)

    reports = diagnose_profiles(list(load_config(config).profiles.values()))

    assert list(reports) == ["hermes", "cursor"]
    assert [report.checked for report in reports.values()] == [2, 2]
    assert [len(report.drift) for report in reports.values()] == [2, 2]
    assert all(report.reused_from == set() for report in reports.values())


def test_a_shared_folder_is_checked_once(tmp_path):
    config = write_config(tmp_path, {"one": ["hermes"], "two": ["hermes"]})
    run_sync(profile_name="one", config_path=config)
    drift_sources(tmp_path)

    reports = diagnose_profiles(list(load_config(config).profiles.values()))

    assert reports["one"].checked == 2
    assert len(reports["one"].findings) == 2
    assert reports["two"].checked == 0
    assert reports["two"].findings == []
    assert reports["two"].reused_from == {"one"}


def test_the_command_reports_every_profile(tmp_path, monkeypatch):
    config = write_config(tmp_path, {"hermes": ["hermes"], "cursor": ["cursor"]})
    sync_all(config)
    drift_sources(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = invoke("doctor", "--all-profiles", "-c", str(config))

    assert result.exit_code == 0, result.output
    assert "All profiles:" in result.output
    assert "4 copy(ies) checked" in result.output
    assert "hermes" in result.output and "cursor" in result.output


def test_a_shared_folder_is_reported_as_covered(tmp_path, monkeypatch):
    config = write_config(tmp_path, {"one": ["hermes"], "two": ["hermes"]})
    run_sync(profile_name="one", config_path=config)
    drift_sources(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = invoke("doctor", "--all-profiles", "-c", str(config))

    assert result.exit_code == 0, result.output
    assert "already checked there" in result.output


def test_the_document_summarises_every_profile(tmp_path, monkeypatch):
    config = write_config(tmp_path, {"hermes": ["hermes"], "cursor": ["cursor"]})
    sync_all(config)
    drift_sources(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = invoke("doctor", "--all-profiles", "--json", "-c", str(config))

    payload = json.loads(result.output)
    assert payload["profile"] is None
    assert [item["profile"] for item in payload["profiles"]] == ["hermes", "cursor"]
    assert [item["checked"] for item in payload["profiles"]] == [2, 2]
    assert payload["checked"] == 4
    assert payload["drift"] == 4
    assert payload["counts"]["out-of-sync"] == 4
    assert {item["profile"] for item in payload["findings"]} == {"hermes", "cursor"}
    assert all(item["files"] for item in payload["findings"])


def test_strict_fails_when_any_profile_drifts(tmp_path, monkeypatch):
    config = write_config(tmp_path, {"hermes": ["hermes"], "cursor": ["cursor"]})
    sync_all(config)
    drift_sources(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = invoke("doctor", "--all-profiles", "--strict", "-c", str(config))

    assert result.exit_code == 1


def test_fix_repairs_every_profile(tmp_path, monkeypatch):
    config = write_config(tmp_path, {"hermes": ["hermes"], "cursor": ["cursor"]})
    sync_all(config)
    drift_sources(tmp_path)
    monkeypatch.chdir(tmp_path)

    fixed = invoke("doctor", "--all-profiles", "--fix", "-c", str(config))
    assert fixed.exit_code == 0, fixed.output
    assert "No drift left." in fixed.output

    strict = invoke("doctor", "--all-profiles", "--strict", "-c", str(config))
    assert strict.exit_code == 0, strict.output


def test_all_profiles_and_a_single_profile_are_mutually_exclusive(tmp_path, monkeypatch):
    config = write_config(tmp_path, {"hermes": ["hermes"]})
    monkeypatch.chdir(tmp_path)

    result = invoke(
        "doctor", "--all-profiles", "--profile", "hermes", "-c", str(config)
    )

    assert result.exit_code == 1
    assert "drop --profile" in result.output
