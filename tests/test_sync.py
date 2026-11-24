from pathlib import Path

import pytest
import yaml

from skill_bridge.agent_paths import global_skill_root, resolve_skill_path
from skill_bridge.config import init_config_interactive, save_config, load_config
from skill_bridge.models import AgentFormat
from skill_bridge.sync import reverse_convert, sync_profile


def test_global_paths():
    p = global_skill_root(AgentFormat.CLAUDE_CODE)
    assert p.name == "skills"
    assert ".claude" in str(p)


def test_resolve_custom_path(tmp_path):
    custom = tmp_path / "my-skills"
    custom.mkdir()
    resolved = resolve_skill_path(AgentFormat.HERMES, override=custom)
    assert resolved == custom.resolve()


def test_init_and_save_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = init_config_interactive(
        source_format=AgentFormat.CODEX,
        target_formats=[AgentFormat.CLAUDE_CODE],
    )
    path = save_config(cfg, tmp_path / "skill-bridge.yaml")
    loaded = load_config(path)
    assert loaded.default_profile == "default"
    assert loaded.profiles["default"].source.format == AgentFormat.CODEX
    assert len(loaded.profiles["default"].targets) == 1


def test_sync_profile(tmp_path):
    src = tmp_path / "source" / "demo-skill"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo skill for sync tests.\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    tgt = tmp_path / "target"

    from skill_bridge.config import SkillLocation, SyncProfile

    profile = SyncProfile(
        name="test",
        source=SkillLocation(format=AgentFormat.GENERIC, path=tmp_path / "source", scope="custom"),
        targets=[SkillLocation(format=AgentFormat.HERMES, path=tgt, scope="custom")],
    )
    results = sync_profile(profile)
    assert len(results) == 1
    assert (tgt / "demo-skill" / "SKILL.md").is_file()


def test_sync_skill_matches_the_frontmatter_slug(tmp_path):
    """A skill is named by its frontmatter, not by its folder."""
    src = tmp_path / "source" / "My Fancy Skill"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: my-fancy-skill\ndescription: Folder and name differ here.\n---\n\n# Fancy\n",
        encoding="utf-8",
    )
    target = tmp_path / "target"

    from skill_bridge.config import SkillLocation, SyncProfile

    profile = SyncProfile(
        name="test",
        source=SkillLocation(format=AgentFormat.GENERIC, path=tmp_path / "source"),
        targets=[SkillLocation(format=AgentFormat.HERMES, path=target)],
    )

    results = sync_profile(profile, skill_name="my-fancy-skill")

    assert [r.skill_name for r in results] == ["my-fancy-skill"]
    assert (target / "my-fancy-skill" / "SKILL.md").is_file()


def test_sync_unknown_skill_lists_the_sources(tmp_path):
    from skill_bridge.config import SkillLocation, SyncProfile

    src = tmp_path / "source" / "demo"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill for sync tests.\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    profile = SyncProfile(
        name="test",
        source=SkillLocation(format=AgentFormat.GENERIC, path=tmp_path / "source"),
        targets=[SkillLocation(format=AgentFormat.HERMES, path=tmp_path / "target")],
    )

    with pytest.raises(FileNotFoundError, match="not found"):
        sync_profile(profile, skill_name="no-such-skill")


def test_reverse_convert_custom_paths(tmp_path):
    src_root = tmp_path / "hermes" / "skills" / "my-skill"
    src_root.mkdir(parents=True)
    (src_root / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Test reverse conversion path.\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    tgt_root = tmp_path / "claude" / "skills"

    results = reverse_convert(
        AgentFormat.HERMES,
        AgentFormat.CLAUDE_CODE,
        source_path=tmp_path / "hermes" / "skills",
        target_path=tgt_root,
    )
    assert len(results) == 1
    assert (tgt_root / "my-skill" / "SKILL.md").is_file()
