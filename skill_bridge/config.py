"""Profile-based configuration for skill-bridge."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from skill_bridge.agent_paths import expand_path, global_skill_root, resolve_skill_path
from skill_bridge.models import AgentFormat

DEFAULT_CONFIG_NAME = "skill-bridge.yaml"
AUTO_SOURCE_TOKENS = {"auto", "all", "*"}


@dataclass
class SkillLocation:
    format: AgentFormat
    path: Path
    scope: str = "global"  # global | project | custom

    @classmethod
    def from_dict(cls, data: dict[str, Any] | str, project: Path | None = None) -> SkillLocation:
        if isinstance(data, str):
            data = {"format": data}
        fmt = AgentFormat.from_string(str(data["format"]))
        scope = str(data.get("scope", "custom" if data.get("path") else "global"))
        path_raw = data.get("path")
        path = resolve_skill_path(fmt, scope=scope, project=project, override=path_raw)
        return cls(format=fmt, path=path, scope=scope)


@dataclass
class SyncProfile:
    name: str
    source: SkillLocation | None = None
    targets: list[SkillLocation] = field(default_factory=list)
    sources: list[SkillLocation] = field(default_factory=list)
    auto_sources: bool = False
    #: Scope used to resolve ``sources: auto`` (``global`` or ``project``).
    source_scope: str = "global"
    watch: bool = True
    validate: bool = True

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any], project: Path | None = None) -> SyncProfile:
        raw_sources = data.get("sources")
        sources: list[SkillLocation] = []
        auto_sources = False
        if isinstance(raw_sources, str):
            if raw_sources.strip().lower() in AUTO_SOURCE_TOKENS:
                auto_sources = True
            else:
                sources = [SkillLocation.from_dict(raw_sources, project=project)]
        elif isinstance(raw_sources, list):
            sources = [SkillLocation.from_dict(item, project=project) for item in raw_sources]

        source = (
            SkillLocation.from_dict(data["source"], project=project)
            if data.get("source")
            else None
        )
        if source is None and not sources and not auto_sources:
            raise ValueError(
                f"Profile '{name}' needs 'source' or 'sources' "
                "(use 'sources: auto' to import from every other harness)"
            )

        return cls(
            name=name,
            source=source,
            targets=[SkillLocation.from_dict(t, project=project) for t in data.get("targets", [])],
            sources=sources,
            auto_sources=auto_sources,
            source_scope=str(data.get("scope", "global")),
            watch=bool(data.get("watch", True)),
            validate=bool(data.get("validate", True)),
        )


@dataclass
class BridgeConfig:
    profiles: dict[str, SyncProfile] = field(default_factory=dict)
    default_profile: str = "default"
    config_path: Path | None = None

    def get_profile(self, name: str | None = None) -> SyncProfile:
        key = name or self.default_profile
        if key not in self.profiles:
            valid = ", ".join(self.profiles) or "(none)"
            raise KeyError(f"Profile '{key}' not found. Available: {valid}")
        return self.profiles[key]


def default_config(project: Path | None = None) -> BridgeConfig:
    """Sensible starter profile: Claude Code -> Hermes + Codex + Cursor."""
    project = project or Path.cwd()
    source = SkillLocation(
        format=AgentFormat.CLAUDE_CODE,
        path=global_skill_root(AgentFormat.CLAUDE_CODE),
        scope="global",
    )
    targets = [
        SkillLocation(format=AgentFormat.HERMES, path=global_skill_root(AgentFormat.HERMES), scope="global"),
        SkillLocation(format=AgentFormat.CODEX, path=global_skill_root(AgentFormat.CODEX), scope="global"),
        SkillLocation(format=AgentFormat.CURSOR, path=global_skill_root(AgentFormat.CURSOR), scope="global"),
    ]
    profile = SyncProfile(name="default", source=source, targets=targets)
    return BridgeConfig(profiles={"default": profile}, default_profile="default")


def _dedupe_locations(locations: Iterable[SkillLocation]) -> list[SkillLocation]:
    """Keep the first location for each real skill directory."""
    unique: list[SkillLocation] = []
    seen: set[str] = set()
    for loc in locations:
        key = os.path.realpath(loc.path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(loc)
    return unique


def auto_import_config(
    target: AgentFormat | Iterable[AgentFormat],
    *,
    from_formats: list[AgentFormat] | None = None,
    scope: str = "global",
    project: Path | None = None,
    target_path: Path | None = None,
) -> BridgeConfig:
    """Profile that pulls skills from every other harness into ``target``.

    ``target`` may be one harness or several (``install --all``); several gives
    one profile with several targets, so a single ``sync`` refreshes them all.
    ``scope`` keeps the written profile pointed at the directory the import
    actually wrote to: ``install --scope project`` targets the project root, so
    ``sync`` and ``watch`` refresh that copy instead of a separate global one.
    ``scope="both"`` covers the global and project copies of each target.
    """
    if isinstance(target, AgentFormat):
        wanted = [target]
    else:
        wanted = list(target)

    target_formats: list[AgentFormat] = []
    for fmt in wanted:
        if fmt is not AgentFormat.GENERIC and fmt not in target_formats:
            target_formats.append(fmt)
    if not target_formats:
        raise ValueError("auto_import_config needs at least one target harness")
    if target_path and len(target_formats) > 1:
        raise ValueError("target_path only makes sense for a single target harness")

    resolved_scope = "project" if scope == "project" else "global"
    # ``both`` writes and refreshes both copies; every other scope is one copy.
    scopes = ["global", "project"] if scope == "both" else [resolved_scope]
    # Harnesses can share a directory (Codex and Amp both use .agents/skills);
    # writing one target per format would convert every skill twice.
    targets = _dedupe_locations(
        SkillLocation(
            format=fmt,
            path=resolve_skill_path(
                fmt, scope=one_scope, project=project, override=target_path
            ),
            scope="custom" if target_path else one_scope,
        )
        for fmt in target_formats
        for one_scope in scopes
    )
    sources: list[SkillLocation] = []
    for fmt in from_formats or []:
        if fmt in target_formats:
            continue
        for one_scope in scopes:
            sources.append(
                SkillLocation(
                    format=fmt,
                    path=resolve_skill_path(fmt, scope=one_scope, project=project),
                    scope=one_scope,
                )
            )

    profile = SyncProfile(
        name="auto-import",
        source=None,
        targets=targets,
        sources=sources,
        auto_sources=not sources,
        source_scope="both" if scope == "both" else resolved_scope,
        watch=True,
        validate=True,
    )
    return BridgeConfig(profiles={"auto-import": profile}, default_profile="auto-import")


def update_auto_import_config(
    existing: BridgeConfig,
    target: AgentFormat | Iterable[AgentFormat],
    *,
    from_formats: list[AgentFormat] | None = None,
    scope: str = "global",
    project: Path | None = None,
    target_path: Path | None = None,
    replace_targets: bool = False,
) -> BridgeConfig:
    """Refresh the ``auto-import`` profile in ``existing`` for ``target``.

    Targets already in the profile are kept, so a harness whose directory is
    missing right now is not dropped, and newly detected harnesses are added.
    ``replace_targets`` writes exactly ``target`` instead: an explicit
    ``install --harness hermes --scope project`` must move the profile to the
    project directory rather than keep refreshing the global one.
    An explicit ``sources:`` list the user edited and every other profile are
    preserved; passing ``from_formats`` replaces the sources as usual.
    """
    cfg = auto_import_config(
        target,
        from_formats=from_formats,
        scope=scope,
        project=project,
        target_path=target_path,
    )
    profile = cfg.profiles["auto-import"]
    old = existing.profiles.get("auto-import")
    if old is not None:
        if not replace_targets:
            profile.targets = _dedupe_locations([*old.targets, *profile.targets])
        if not profile.sources and old.sources:
            profile.sources = list(old.sources)
            profile.auto_sources = False
    for name, other in existing.profiles.items():
        if name != "auto-import":
            cfg.profiles[name] = other
    if existing.default_profile in cfg.profiles:
        cfg.default_profile = existing.default_profile
    return cfg


def _portable_path(path: Path, base: Path | None) -> str:
    """``path`` relative to the config's directory when it sits underneath it.

    A committed config has to mean the same thing in a fresh clone, and an
    absolute path does not: it names the checkout that generated the file.
    """
    if base is not None:
        try:
            return Path(os.path.relpath(path, base)).as_posix()
        except ValueError:  # different drives on Windows
            pass
    return str(path)


def config_to_dict(
    config: BridgeConfig, *, base: Path | None = None
) -> dict[str, Any]:
    def loc_dict(loc: SkillLocation) -> dict[str, Any]:
        d: dict[str, Any] = {"format": loc.format.value}
        if loc.scope != "global":
            d["scope"] = loc.scope
        # Always write explicit path for clarity in user configs
        d["path"] = _portable_path(loc.path, base)
        return d

    profiles: dict[str, Any] = {}
    for name, profile in config.profiles.items():
        entry: dict[str, Any] = {}
        if profile.auto_sources:
            entry["sources"] = "auto"
        elif profile.sources:
            entry["sources"] = [loc_dict(s) for s in profile.sources]
        if profile.source is not None:
            entry["source"] = loc_dict(profile.source)
        entry["targets"] = [loc_dict(t) for t in profile.targets]
        if profile.source_scope != "global":
            entry["scope"] = profile.source_scope
        entry["watch"] = profile.watch
        entry["validate"] = profile.validate
        profiles[name] = entry

    return {
        "default_profile": config.default_profile,
        "profiles": profiles,
    }


def save_config(config: BridgeConfig, path: Path | None = None) -> Path:
    path = path or Path.cwd() / DEFAULT_CONFIG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            config_to_dict(config, base=path.parent),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config.config_path = path
    return path


def _config_candidates(project: Path) -> list[Path]:
    """Every place ``load_config`` looks for a config, in priority order."""
    return [
        project / DEFAULT_CONFIG_NAME,
        project / ".skill-bridge" / DEFAULT_CONFIG_NAME,
        Path.home() / DEFAULT_CONFIG_NAME,
    ]


def load_config(path: Path | None = None, project: Path | None = None) -> BridgeConfig:
    project = project or Path.cwd()
    candidates = [path] if path else _config_candidates(project)
    for candidate in candidates:
        if candidate and candidate.is_file():
            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError(f"Invalid config file: {candidate}")
            profiles_raw = data.get("profiles") or {}
            # Relative paths in the file mean relative to the file: that is what
            # makes a committed config survive a clone at another path.
            base = candidate.parent
            profiles = {
                name: SyncProfile.from_dict(name, pdata, project=base)
                for name, pdata in profiles_raw.items()
            }
            cfg = BridgeConfig(
                profiles=profiles,
                default_profile=str(data.get("default_profile", "default")),
                config_path=candidate,
            )
            return cfg
    raise FileNotFoundError(
        f"No {DEFAULT_CONFIG_NAME} found. Run: skill-bridge init"
    )


@dataclass
class ForgottenTargets:
    """Config files changed by forgetting the targets ``uninstall`` emptied."""

    updated: list[Path] = field(default_factory=list)
    deleted: list[Path] = field(default_factory=list)


def forget_targets(
    locations: Iterable[SkillLocation],
    *,
    config: Path | None = None,
    project: Path | None = None,
) -> ForgottenTargets:
    """Drop profile targets that point at skill folders being uninstalled.

    Deleting the copies is only half of an uninstall: the profile that
    produced them stays in the config, so the next ``sync`` (or a running
    ``watch``) puts every one of them back. This removes exactly the targets
    that name one of ``locations``.

    Matching is by real path, so uninstalling one directory never disturbs a
    profile aimed at another, and a user's own profiles are only touched when
    they point at the removed directory too. A profile left without any target
    is dropped; a config file left without any profile is deleted.

    ``config`` names one file explicitly, for a profile kept outside the
    directories ``load_config`` searches; only that file is considered then.
    """
    project = project or Path.cwd()
    gone = {os.path.realpath(loc.path) for loc in locations}
    result = ForgottenTargets()
    if not gone:
        return result

    seen: set[str] = set()
    candidates = [config] if config else _config_candidates(project)
    for path in candidates:
        key = os.path.realpath(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        try:
            config = load_config(path, project=project)
        except (OSError, ValueError, yaml.YAMLError):
            continue  # a config we cannot read is not ours to rewrite

        changed = False
        for name in list(config.profiles):
            profile = config.profiles[name]
            kept = [
                target
                for target in profile.targets
                if os.path.realpath(target.path) not in gone
            ]
            if len(kept) == len(profile.targets):
                continue
            changed = True
            profile.targets = kept
            if not kept:
                del config.profiles[name]
        if not changed:
            continue

        if config.profiles:
            if config.default_profile not in config.profiles:
                config.default_profile = next(iter(config.profiles))
            save_config(config, path)
            result.updated.append(path)
            continue

        path.unlink()
        result.deleted.append(path)
        # Do not leave an empty .skill-bridge/ behind after its only file goes.
        if path.parent.name == ".skill-bridge" and not any(path.parent.iterdir()):
            path.parent.rmdir()
    return result


def init_config_interactive(
    *,
    source_format: AgentFormat | None = None,
    target_formats: list[AgentFormat] | None = None,
    project: Path | None = None,
) -> BridgeConfig:
    project = project or Path.cwd()
    src_fmt = source_format or AgentFormat.CLAUDE_CODE
    tgt_fmts = target_formats or [
        AgentFormat.HERMES,
        AgentFormat.CODEX,
        AgentFormat.CURSOR,
    ]

    source = SkillLocation(format=src_fmt, path=global_skill_root(src_fmt), scope="global")
    targets = [
        SkillLocation(format=f, path=global_skill_root(f), scope="global") for f in tgt_fmts
    ]
    profile = SyncProfile(name="default", source=source, targets=targets)
    return BridgeConfig(profiles={"default": profile}, default_profile="default")
