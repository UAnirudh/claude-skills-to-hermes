"""doctor's conversion cache: the same answer, without converting twice.

Re-converting a source is how `doctor` says which files inside a behind copy
differ, and it is the expensive half of a pass. The result depends only on the
source, the target format and the converter's code, so it is kept and read back
on the next check. What has to stay true: the report is identical either way, a
changed source or a changed converter is never served stale output, and a
broken entry is just a miss.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from skill_bridge import cache
from skill_bridge import doctor as doctor_module
from skill_bridge.config import load_config
from skill_bridge.doctor import diagnose
from skill_bridge.models import AgentFormat
from skill_bridge.sync import run_sync

NAMES = ("demo", "bravo")


def write_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name} used by the doctor cache tests.\n"
        "---\n\n"
        f"# {name}\n\n"
        "Body paragraph.\n",
        encoding="utf-8",
    )
    return skill_dir


def write_config(tmp_path: Path, targets: tuple[str, ...] = ("hermes",)) -> Path:
    source = tmp_path / "src"
    for name in NAMES:
        write_skill(source, name)
    config = {
        "default_profile": "test",
        "profiles": {
            "test": {
                "source": {"format": "generic", "path": str(source)},
                "targets": [
                    {"format": fmt, "path": str(tmp_path / f".{fmt}" / "skills")}
                    for fmt in targets
                ],
            }
        },
    }
    path = tmp_path / "skill-bridge.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def drifted(tmp_path: Path, targets: tuple[str, ...] = ("hermes",)) -> Path:
    """A profile whose copies are all behind their sources."""
    config = write_config(tmp_path, targets)
    run_sync(config_path=config)
    for name in NAMES:
        path = tmp_path / "src" / name / "SKILL.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "\n## Added later\n", encoding="utf-8"
        )
    return config


def targets_of(config: Path):
    return load_config(config).get_profile("test").targets


def view(report):
    return [
        (finding.kind, finding.skill_name, finding.detail, finding.files)
        for finding in report.findings
    ]


def count_conversions(monkeypatch) -> list[Path]:
    """Record every conversion doctor makes, and still make it."""
    calls: list[Path] = []
    real = doctor_module.convert_skill

    def wrapper(*args, **kwargs):
        calls.append(Path(args[0]))
        return real(*args, **kwargs)

    monkeypatch.setattr(doctor_module, "convert_skill", wrapper)
    return calls


def forbid_conversion(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("doctor converted a source it had already converted")

    monkeypatch.setattr(doctor_module, "convert_skill", boom)


def test_a_second_check_reads_the_conversion_back(tmp_path, monkeypatch):
    config = drifted(tmp_path)
    first = diagnose(targets_of(config))
    assert [finding.kind for finding in first.findings] == ["out-of-sync"] * 2

    forbid_conversion(monkeypatch)
    second = diagnose(targets_of(config))

    assert view(second) == view(first)
    assert second.checked == first.checked == 2


def test_the_first_check_converts_every_copy(tmp_path, monkeypatch):
    config = drifted(tmp_path)
    calls = count_conversions(monkeypatch)

    diagnose(targets_of(config))

    assert len(calls) == 2


def test_the_cache_is_keyed_by_the_target_format(tmp_path, monkeypatch):
    config = drifted(tmp_path, targets=("hermes", "cursor"))
    diagnose(targets_of(config))
    # Two sources times two targets: the target is part of the key, so neither
    # conversion can answer for the other.
    assert len(cache._entries(cache.cache_root())) == 4

    forbid_conversion(monkeypatch)
    report = diagnose(targets_of(config))

    assert len(report.findings) == 4
    assert {finding.target_format.value for finding in report.findings} == {
        "hermes",
        "cursor",
    }


def test_a_changed_source_is_converted_again(tmp_path, monkeypatch):
    config = drifted(tmp_path)
    diagnose(targets_of(config))

    path = tmp_path / "src" / "demo" / "SKILL.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n## Later still\n", encoding="utf-8"
    )
    calls = count_conversions(monkeypatch)
    report = diagnose(targets_of(config))

    # The copy whose source changed is converted; the untouched one keeps the
    # fingerprints that let doctor skip the conversion entirely.
    assert [call.name for call in calls] == ["demo"]
    # Both copies are still behind, but only one had to be converted: the other
    # was answered from the cache, and the report is the same either way.
    assert sorted(finding.skill_name for finding in report.findings) == [
        "bravo",
        "demo",
    ]


def test_new_converter_code_is_not_served_stale_output(tmp_path, monkeypatch):
    config = drifted(tmp_path)
    diagnose(targets_of(config))

    # A converter fix that never changed the reported version: the digest is
    # what identifies the code, so the stored output no longer applies.
    monkeypatch.setattr(cache, "code_digest", lambda: "different-code")
    calls = count_conversions(monkeypatch)

    report = diagnose(targets_of(config))

    assert len(calls) == 2
    assert [finding.kind for finding in report.findings] == ["out-of-sync"] * 2


def test_a_broken_entry_is_a_miss_and_is_repaired(tmp_path, monkeypatch):
    config = drifted(tmp_path)
    diagnose(targets_of(config))
    entry = cache._entries(cache.cache_root())[0]
    (entry / cache.MANIFEST).unlink()  # a half-written entry is not trusted

    calls = count_conversions(monkeypatch)
    diagnose(targets_of(config))
    assert len(calls) == 1  # only the broken entry had to be converted again

    forbid_conversion(monkeypatch)
    diagnose(targets_of(config))


def test_entries_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "MAX_ENTRIES", 1)
    config = drifted(tmp_path, targets=("hermes", "cursor"))

    diagnose(targets_of(config))

    assert len(cache._entries(cache.cache_root())) == 1


def test_a_clean_profile_converts_nothing(tmp_path, monkeypatch):
    config = write_config(tmp_path)
    run_sync(config_path=config)
    forbid_conversion(monkeypatch)

    assert diagnose(targets_of(config)).findings == []


def test_entries_live_where_the_environment_says(tmp_path):
    # tests/conftest.py points the cache at the test's own directory, which is
    # also what someone with an unusual home directory would set.
    assert cache.cache_root() == tmp_path / "cache"


def test_the_key_covers_the_source_the_target_and_the_code():
    hermes = cache.key_for("abc", AgentFormat.HERMES)

    assert hermes == cache.key_for("abc", AgentFormat.HERMES)
    assert hermes != cache.key_for("abd", AgentFormat.HERMES)
    assert hermes != cache.key_for("abc", AgentFormat.CURSOR)
    assert cache.key_for("", AgentFormat.HERMES) is None
