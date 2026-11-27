"""Skill Bridge CLI."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from skill_bridge.adopt import AdoptReport, run_adopt
from skill_bridge.annotations import annotations as annotation_lines
from skill_bridge.annotations import sarif as sarif_log
from skill_bridge.agent_paths import (
    detect_installed_agents,
    global_skill_root,
    resolve_skill_path,
)
from skill_bridge.compat import lossy_fields
from skill_bridge.config import (
    DEFAULT_CONFIG_NAME,
    BridgeConfig,
    SkillLocation,
    SyncProfile,
    auto_import_config,
    forget_targets,
    init_config_interactive,
    load_config,
    save_config,
    update_auto_import_config,
)
from skill_bridge.converter import convert_batch, convert_skill
from skill_bridge.diff import diff_profile, render as render_diff
from skill_bridge.lint import lint_matrix, lint_skill
from skill_bridge.locking import DEFAULT_LOCK_WAIT, LockTimeout
from skill_bridge.doctor import (
    EDITED,
    LOSSY,
    OUTDATED,
    OUT_OF_SYNC,
    STALE,
    UNFETCHED,
    DoctorReport,
    RepairResult,
    accept_edits,
    diagnose,
    diagnose_profiles,
    profiles_to_dict,
    repair,
    report_schema,
    report_to_dict,
)
from skill_bridge.formats.base import all_exporters, get_exporter, load_exporters
from skill_bridge.models import AgentFormat
from skill_bridge.parser import find_skills, load_skill
from skill_bridge.provenance import is_bridge_generated, read_marker
from skill_bridge.prune import RemovalPlan, RemovalItem, apply_removals, plan_removals
from skill_bridge.remote import RemoteError, checkout
from skill_bridge.sync import reverse_convert, run_prune, run_sync
from skill_bridge.validator import validate_skill
from skill_bridge.watch import watch_from_config, watch_roots

console = Console()
OK = "[green]OK[/green]"
WARN = "[yellow]![/yellow]"
ARROW = "->"

ACTION_STYLES = {
    "import": "green",
    "update": "cyan",
    "overwrite": "yellow",
    "skip": "dim",
    "error": "red",
}


def _parse_format(value: str) -> AgentFormat:
    """Harness id or alias -> AgentFormat, as a clean CLI error when unknown."""
    try:
        return AgentFormat.from_string(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc


def _print_results(results, *, show_path: bool = True) -> None:
    for r in results:
        loc = f" ({r.output_dir})" if show_path else ""
        console.print(f"{OK} {r.skill_name} {ARROW} {r.target_format.value}{loc}")
        for w in r.warnings:
            console.print(f"  {WARN} {w}")


def _print_removals(
    plan: RemovalPlan,
    removed: list[RemovalItem],
    *,
    dry_run: bool,
    action: str,
    dry_run_action: str,
) -> None:
    """Report a prune/uninstall: what went (or would go), and what stayed."""
    verb = dry_run_action if dry_run else action
    for item in plan.items:
        console.print(
            f"[red]{verb:<13}[/] {item.skill_name} ({item.target_format.value}) "
            f"[dim]{item.reason}[/dim]"
        )
    if plan.kept_native and action == "remove":
        console.print(
            f"[dim]Left {len(plan.kept_native)} hand-written skill(s) alone."
            "[/dim]"
        )
    if plan.pending:
        console.print(
            f"{WARN} {len(plan.pending)} copy(ies) wait for a repository that is "
            "not checked out; run skill-bridge fetch before pruning."
        )
    if not plan.items:
        console.print("[dim]Nothing to remove.[/dim]")
        return
    if dry_run:
        console.print(
            f"\n[bold]Would {action} {len(plan.items)} skill(s)[/bold]. "
            "Re-run without --dry-run to remove them."
        )
    else:
        console.print(
            f"\n[bold]{action.capitalize()}d {len(removed)} skill(s)[/bold]"
        )
        kept = len(plan.items) - len(removed)
        if kept:
            console.print(
                f"{WARN} {kept} skill(s) changed while planning and were kept"
            )


DOCTOR_SECTIONS = (
    (
        UNFETCHED,
        "Not fetched",
        "skill-bridge fetch checks these repositories out",
        "bright_black",
    ),
    (STALE, "Source gone", "skill-bridge sync --prune removes these", "red"),
    (OUT_OF_SYNC, "Out of sync", "--fix refreshes these from their source", "yellow"),
    (
        OUTDATED,
        "Converted by an older skill-bridge",
        "sync re-converts these",
        "blue",
    ),
    (EDITED, "Edited by hand", "--fix leaves these alone: sync would overwrite", "magenta"),
    (LOSSY, "Lossy fields", "kept in metadata.skill-bridge.extra", "cyan"),
)


def _print_doctor(report: DoctorReport) -> None:
    """Group findings by kind, each section next to the command that fixes it."""
    for kind, title, hint, colour in DOCTOR_SECTIONS:
        findings = report.of_kind(kind)
        if not findings:
            continue
        console.print(f"\n[bold]{title}[/bold] [dim]({hint})[/dim]")
        for finding in findings:
            console.print(
                f"  [{colour}]{finding.skill_name}[/{colour}] "
                f"({finding.target_format.value}) [dim]{finding.detail}[/dim]"
            )
    unfetched = len(report.of_kind(UNFETCHED))
    stale = len(report.of_kind(STALE))
    behind = len(report.of_kind(OUT_OF_SYNC))
    outdated = len(report.of_kind(OUTDATED))
    edited = len(report.of_kind(EDITED))
    lossy = len(report.of_kind(LOSSY))
    console.print()
    if report.clean:
        console.print(
            f"{OK} {report.checked} generated copy(ies) checked, nothing to fix."
        )
        return
    parts = [
        f"{stale} stale",
        f"{behind} out of sync",
        f"{outdated} outdated",
        f"{edited} edited",
        f"{lossy} lossy",
    ]
    if unfetched:
        # Named only when it happened: a machine that has every checkout does
        # not need a zero about a state it is not in.
        parts.insert(0, f"{unfetched} not fetched")
    console.print(
        f"[bold]Checked {report.checked} generated copy(ies):[/bold] "
        + ", ".join(parts)
    )


def _print_diff(lines: list[str]) -> None:
    """Colour a unified diff, leaving its text exactly as the file has it.

    ``markup=False`` on purpose: a skill's own prose can contain square
    brackets, and a diff is the one place where the text has to survive intact.
    """
    for line in lines:
        if line.startswith(("--- ", "+++ ")):
            style = "bold"
        elif line.startswith("@@"):
            style = "cyan"
        elif line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        else:
            style = None
        console.print(line, style=style, markup=False, highlight=False)


def _print_repair(result: RepairResult, after: DoctorReport) -> None:
    """Say what --fix changed, and whether the profile is drift-free now."""
    console.print()
    if result.touched:
        console.print(
            f"[bold]Fixed[/bold] {result.pruned} stale, {result.refreshed} out of sync"
            f"[dim] ({result.conversions} conversion(s))[/dim]"
        )
    if result.edited:
        console.print(
            f"{WARN} Left {result.edited} hand-edited copy(ies) alone; "
            "run sync to regenerate one deliberately."
        )
    if result.lossy:
        console.print(
            f"[dim]Left {result.lossy} lossy finding(s) alone: "
            "no conversion restores them.[/dim]"
        )
    if after.of_kind(OUTDATED):
        console.print(
            f"[dim]{len(after.of_kind(OUTDATED))} copy(ies) came from an older "
            "skill-bridge; sync re-converts them.[/dim]"
        )
    if after.drift:
        console.print(
            f"{WARN} {len(after.drift)} drift finding(s) remain; run doctor to inspect."
        )
    else:
        console.print(f"{OK} No drift left.")


def _print_adopt(report: AdoptReport, *, dry_run: bool = False) -> None:
    if not report.items:
        console.print("[yellow]No skills found in any other harness.[/yellow]")
        console.print(
            "Author or install skills somewhere, or pick sources explicitly: "
            "skill-bridge adopt --to <harness> --from <harness>"
        )
        return

    for item in report.items:
        style = ACTION_STYLES.get(item.action, "white")
        console.print(
            f"[{style}]{item.action:<9}[/] {item.skill_name} "
            f"({item.source_format.value}) {ARROW} {report.target_format.value}"
        )
        if item.reason:
            console.print(f"          [dim]{item.reason}[/dim]")
        for w in item.warnings:
            console.print(f"  {WARN} {w}")

    counts = Counter(item.action for item in report.items)
    verb = "Would import" if dry_run else "Imported"
    console.print(
        f"\n[bold]{verb}[/bold] {len(report.selected())} skill(s) into "
        f"[cyan]{report.target_format.value}[/cyan] "
        f"({report.target_root})\n"
        f"  import={counts['import']} update={counts['update']} "
        f"overwrite={counts['overwrite']} skip={counts['skip']} error={counts['error']}"
    )
    if dry_run:
        console.print("[dim]Dry run: nothing written. Re-run without --dry-run.[/dim]")


class _MainGroup(click.Group):
    """Report a busy lock as a usage error, not as a traceback."""

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except LockTimeout as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=_MainGroup)
@click.version_option(package_name="skill-bridge", prog_name="skill-bridge")
def main() -> None:
    """Convert and sync agent skills across Claude Code, Hermes, Codex, Cursor, and every other harness."""


@main.command("list-formats")
def list_formats_cmd() -> None:
    """List supported target agent formats."""
    load_exporters()
    table = Table(title="Supported Agent Formats")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Project path")
    table.add_column("Global path")
    table.add_column("Description")

    for exporter in sorted(all_exporters(), key=lambda e: e.format_id.value):
        fmt = exporter.format_id
        global_path = exporter.global_skill_subdir or exporter.default_output_subdir
        table.add_row(
            fmt.value,
            exporter.display_name,
            exporter.default_output_subdir,
            str(global_skill_root(fmt)),
            exporter.description,
        )
    console.print(table)


@main.command("detect")
@click.option(
    "--scope",
    type=click.Choice(["global", "project", "both"]),
    default="global",
    help="Where to look for harness skill directories",
)
def detect_cmd(scope: str) -> None:
    """Show every supported harness and whether its skill directory exists here."""
    load_exporters()
    scopes = ["global", "project"] if scope == "both" else [scope]
    installed: dict[AgentFormat, tuple[Path, str]] = {}
    for one_scope in scopes:
        for fmt, path in detect_installed_agents(scope=one_scope).items():
            installed.setdefault(fmt, (path, one_scope))
    counts: dict[str, int] = {}

    table = Table(title="Agent harnesses on this machine")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Skill directory")
    table.add_column("Status")
    table.add_column("Skills", justify="right")

    for exporter in sorted(all_exporters(), key=lambda e: e.format_id.value):
        fmt = exporter.format_id
        if fmt is AgentFormat.GENERIC:
            continue
        found = fmt in installed
        if found:
            path, found_scope = installed[fmt]
            key = str(path)
            if key not in counts:
                counts[key] = len(find_skills(path))
            count = str(counts[key])
            status = "[green]detected[/green]"
            if scope == "both":
                status += f" [dim]({found_scope})[/dim]"
        else:
            path = resolve_skill_path(fmt, scope=scopes[0])
            count = "-"
            status = "[dim]not found[/dim]"
        table.add_row(
            fmt.value,
            exporter.display_name,
            str(path),
            status,
            count,
        )
    console.print(table)

    detected = sorted(fmt.value for fmt in installed)
    if not detected:
        console.print(
            "[yellow]No harness skill directories found.[/yellow] "
            "Create one (or run a harness once), then: skill-bridge install --harness <id>"
        )
        return
    console.print(f"[bold]{len(detected)} harness(es) detected:[/bold] {', '.join(detected)}")
    for target in detected:
        others = [d for d in detected if d != target]
        if others:
            console.print(
                f"  Import other harnesses into {target}: "
                f"[cyan]skill-bridge adopt --to {target}[/cyan]"
            )
            break


@main.command("init")
@click.option("--source", "source_format", default="claude-code", help="Primary agent you author skills in")
@click.option(
    "--target",
    "targets",
    multiple=True,
    default=("hermes", "codex", "cursor"),
    help="Agents to sync to (repeatable)",
)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Config file path")
@click.option("--force", is_flag=True, help="Overwrite existing config")
def init_cmd(
    source_format: str,
    targets: tuple[str, ...],
    output: Path | None,
    force: bool,
) -> None:
    """Create skill-bridge.yaml for one-command sync and watch."""
    out_path = output or Path.cwd() / DEFAULT_CONFIG_NAME
    if out_path.exists() and not force:
        console.print(f"[red]Config exists: {out_path}[/red]  Use --force to overwrite")
        raise SystemExit(1)

    src = _parse_format(source_format)
    tgts = [_parse_format(t) for t in targets]
    config = init_config_interactive(source_format=src, target_formats=tgts)
    saved = save_config(config, out_path)

    console.print(f"{OK} Wrote {saved}")
    console.print("\n[bold]Profile 'default':[/bold]")
    console.print(f"  source: {config.profiles['default'].source.format.value} -> {config.profiles['default'].source.path}")
    for t in config.profiles["default"].targets:
        console.print(f"  target: {t.format.value} -> {t.path}")
    console.print("\nNext steps:")
    console.print("  skill-bridge sync          # one-shot sync")
    console.print("  skill-bridge watch         # live sync on file changes")
    console.print("  skill-bridge adopt --to hermes   # import every other harness")


def _can_prompt() -> bool:
    """Whether asking a question is safe: a real terminal, not a pipe or CI."""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _prompt_for_harness(detected: dict[AgentFormat, Path]) -> list[AgentFormat]:
    """Numbered picker for the harnesses installed here; 'all' picks every one."""
    ordered = sorted(detected, key=lambda fmt: fmt.value)
    all_choice = len(ordered) + 1

    console.print("[bold]Several harnesses are installed here.[/bold]")
    for index, fmt in enumerate(ordered, 1):
        console.print(f"  {index}. {fmt.value:<12} {detected[fmt]}")
    console.print(f"  {all_choice}. all of them")

    while True:
        raw = click.prompt("Install into which harness?", default="1", show_default=False)
        answer = str(raw).strip().lower()
        if answer in {"a", "all", "*"}:
            return ordered
        if answer.isdigit():
            number = int(answer)
            if number == all_choice:
                return ordered
            if 1 <= number <= len(ordered):
                return [ordered[number - 1]]
        console.print(f"[yellow]Enter a number 1-{all_choice}, or 'all'.[/yellow]")


def _unique_roots(
    targets: list[AgentFormat], scope: str, to_path: Path | None
) -> list[tuple[AgentFormat, Path]]:
    """Target roots, one per real directory (Codex and Amp share .agents/skills)."""
    roots: list[tuple[AgentFormat, Path]] = []
    seen: set[str] = set()
    for fmt in targets:
        root = resolve_skill_path(fmt, scope=scope, override=to_path)
        key = os.path.realpath(root)
        if key in seen:
            continue
        seen.add(key)
        roots.append((fmt, root))
    return roots


def _existing_auto_import(path: Path) -> BridgeConfig | None:
    """The config at ``path`` if it already has an 'auto-import' profile."""
    if not path.is_file():
        return None
    try:
        cfg = load_config(path)
    except (OSError, ValueError, yaml.YAMLError):
        # Unreadable configs are never rewritten without --force.
        return None
    return cfg if "auto-import" in cfg.profiles else None


def _detected_harnesses(scopes: list[str]) -> dict[AgentFormat, Path]:
    """Harness skill directories found in any of ``scopes`` (global wins)."""
    found: dict[AgentFormat, Path] = {}
    for one_scope in scopes:
        for fmt, path in detect_installed_agents(scope=one_scope).items():
            found.setdefault(fmt, path)
    return found


def _resolve_install_targets(
    harness: str | None,
    all_harnesses: bool,
    config_path: Path | None,
    scopes: list[str] | None = None,
) -> list[AgentFormat]:
    """Which harness(es) to install into: explicit, detected, picked or --all."""
    if harness and all_harnesses:
        raise click.UsageError("Use --harness or --all, not both")
    if harness:
        return [_parse_format(harness)]

    scopes = scopes or ["global"]
    detected = _detected_harnesses(scopes)
    if all_harnesses:
        if not detected:
            console.print(
                "[red]No harness skill directories found.[/red] "
                "Create one (or run a harness once), then try again."
            )
            raise SystemExit(1)
        return sorted(detected, key=lambda fmt: fmt.value)

    if len(detected) == 1:
        return [next(iter(detected))]

    if len(detected) > 1 and _can_prompt():
        return _prompt_for_harness(detected)

    for candidate in (config_path, Path.cwd() / DEFAULT_CONFIG_NAME):
        if candidate and candidate.is_file():
            try:
                profile = load_config(candidate).get_profile(None)
            except (FileNotFoundError, KeyError, ValueError):
                continue
            if profile.targets:
                return [profile.targets[0].format]

    listing = (
        "\n".join(
            f"  {fmt.value:<12} {path}"
            for fmt, path in sorted(detected.items(), key=lambda kv: kv[0].value)
        )
        or "  (none found)"
    )
    console.print("[red]Could not tell which harness you are installing into.[/red]")
    console.print(f"Harness skill directories found ({' + '.join(scopes)}):\n" + listing)
    console.print("\nRe-run with --harness <id>, or install into every one with --all")
    raise SystemExit(1)


@main.command("install")
@click.option("-H", "--harness", default=None, help="Harness you are installing into (e.g. hermes, cursor)")
@click.option("--all", "all_harnesses", is_flag=True, help="Install into every harness detected on this machine")
@click.option("--from", "from_formats", multiple=True, help="Only import from these harnesses (repeatable)")
@click.option("--scope", type=click.Choice(["global", "project", "both"]), default="global")
@click.option("--to-path", type=click.Path(path_type=Path), default=None, help="Override the target skills directory")
@click.option("--force", is_flag=True, help="Overwrite existing config and native (hand-written) skills")
@click.option("--dry-run", is_flag=True, help="Show what would be imported")
@click.option("--no-sync", is_flag=True, help="Write config only, import nothing yet")
@click.option("--no-validate", is_flag=True, help="Skip validation warnings")
@click.option("-c", "--config", "config_path", type=click.Path(path_type=Path), default=None)
def install_cmd(
    harness: str | None,
    all_harnesses: bool,
    from_formats: tuple[str, ...],
    scope: str,
    to_path: Path | None,
    force: bool,
    dry_run: bool,
    no_sync: bool,
    no_validate: bool,
    config_path: Path | None,
) -> None:
    """Install into your harness: convert skills meant for every other harness.

    Detects the other agent harnesses on this machine and makes their skills work
    in yours. With no --harness it uses the only detected harness, or asks you
    to pick when several are installed (--all installs into every one). Writes an
    'auto-import' profile so `skill-bridge sync` keeps them up to date.
    """
    load_exporters()
    resolved_scopes = (
        ["global", "project"]
        if scope == "both"
        else ["project" if scope == "project" else "global"]
    )
    targets = _resolve_install_targets(harness, all_harnesses, config_path, resolved_scopes)
    if to_path and len(targets) > 1:
        raise click.UsageError("--to-path needs a single target; use --harness")

    roots: list[tuple[AgentFormat, Path, str]] = []
    seen_roots: set[str] = set()
    shared = 0
    for one_scope in resolved_scopes:
        scoped = _unique_roots(targets, one_scope, to_path)
        shared = max(shared, len(targets) - len(scoped))
        for fmt, root in scoped:
            key = os.path.realpath(root)
            if key in seen_roots:
                continue
            seen_roots.add(key)
            roots.append((fmt, root, one_scope))
    if shared:
        console.print(
            f"[dim]{shared} harness(es) share a skill directory; "
            "installing into each directory once[/dim]"
        )

    if len(roots) == 1:
        console.print(f"[bold]Harness:[/bold] {roots[0][0].value}")
        console.print(f"[bold]Skills go to:[/bold] {roots[0][1]}")
    else:
        console.print(
            f"[bold]Harnesses:[/bold] {', '.join(fmt.value for fmt in targets)}"
        )
        if scope == "both":
            console.print(f"[bold]Scopes:[/bold] {', '.join(resolved_scopes)}")

    sources = [_parse_format(f) for f in from_formats]
    cfg_out = config_path or Path.cwd() / DEFAULT_CONFIG_NAME
    existing = _existing_auto_import(cfg_out)
    if dry_run:
        console.print(f"[dim]Dry run: would write {cfg_out}[/dim]")
    elif cfg_out.exists() and not force and existing is None:
        # Never rewrite a config we do not understand, but say what it means.
        destinations = " and ".join(str(root) for _, root, _ in roots)
        console.print(
            f"{WARN} Keeping {cfg_out}: it has no readable 'auto-import' profile, "
            f"so sync and watch will not follow {destinations}."
        )
        console.print("[dim]Re-run with --force to replace the config.[/dim]")
    elif existing is not None and not force:
        cfg = update_auto_import_config(
            existing,
            targets,
            from_formats=sources or None,
            scope=scope,
            target_path=to_path,
            replace_targets=not all_harnesses,
        )
        saved = save_config(cfg, cfg_out)
        console.print(f"{OK} Updated {saved} (profile 'auto-import')")
    else:
        cfg = auto_import_config(
            targets,
            from_formats=sources or None,
            scope=scope,
            target_path=to_path,
        )
        saved = save_config(cfg, cfg_out)
        console.print(f"{OK} Wrote {saved} (profile 'auto-import')")

    if no_sync:
        console.print("\nNext: skill-bridge sync")
        return

    for target, target_root, write_scope in roots:
        if len(roots) > 1:
            console.print(f"\n[bold]Harness:[/bold] {target.value}")
            console.print(f"[bold]Skills go to:[/bold] {target_root}")
        report = run_adopt(
            target,
            from_formats=sources or None,
            scope=write_scope,
            target_path=to_path,
            force=force,
            validate=not no_validate,
            dry_run=dry_run,
        )
        _print_adopt(report, dry_run=dry_run)
    if not dry_run:
        console.print("\nKeep it current: [cyan]skill-bridge sync[/cyan] or [cyan]skill-bridge watch[/cyan]")


@main.command("uninstall")
@click.option("-H", "--harness", default=None, help="Harness to clean (e.g. hermes)")
@click.option(
    "--all",
    "all_harnesses",
    is_flag=True,
    help="Clean every harness detected on this machine",
)
@click.option("--scope", type=click.Choice(["global", "project", "both"]), default="global")
@click.option(
    "--to-path",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the target skills directory",
)
@click.option(
    "-c",
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Config to update (default: the one sync would read)",
)
@click.option(
    "--stale-only",
    is_flag=True,
    help="Only copies whose source was deleted or renamed",
)
@click.option("--dry-run", is_flag=True, help="Report what would be removed, change nothing")
def uninstall_cmd(
    harness: str | None,
    all_harnesses: bool,
    scope: str,
    to_path: Path | None,
    config_path: Path | None,
    stale_only: bool,
    dry_run: bool,
) -> None:
    """Remove the skills skill-bridge created in a harness.

    Copies carry a .skill-bridge.json marker, so skills the harness authored
    itself are left alone. --stale-only deletes just the copies whose source
    was deleted or renamed (the same set `sync --prune` removes).
    """
    load_exporters()
    if bool(harness) == bool(all_harnesses):
        raise click.UsageError("Use --harness <id> or --all, not both")
    if to_path and all_harnesses:
        raise click.UsageError("--to-path needs a single target; use --harness")

    scopes = ["global", "project"] if scope == "both" else [scope]
    if harness:
        targets = [_parse_format(harness)]
    else:
        detected = _detected_harnesses(scopes)
        if not detected:
            console.print(
                "[yellow]No harness skill directories found.[/yellow] "
                "Nothing to uninstall."
            )
            return
        targets = sorted(detected, key=lambda fmt: fmt.value)

    locations: list[SkillLocation] = []
    seen: set[str] = set()
    for one_scope in scopes:
        for fmt, root in _unique_roots(targets, one_scope, to_path):
            key = os.path.realpath(root)
            if key in seen:
                continue
            seen.add(key)
            locations.append(
                SkillLocation(
                    format=fmt,
                    path=root,
                    scope="custom" if to_path else one_scope,
                )
            )

    console.print(f"[bold]Harness:[/bold] {', '.join(fmt.value for fmt in targets)}")
    missing = [loc for loc in locations if not loc.path.is_dir()]
    for loc in locations:
        state = "" if loc.path.is_dir() else " [dim](not found)[/dim]"
        console.print(f"[bold]Skills in:[/bold] {loc.path}{state}")
    if len(missing) == len(locations):
        return

    plan = plan_removals(
        [loc for loc in locations if loc.path.is_dir()], stale_only=stale_only
    )
    removed = [] if dry_run else apply_removals(plan.items)
    console.print()
    _print_removals(
        plan,
        removed,
        dry_run=dry_run,
        action="remove",
        dry_run_action="would remove",
    )

    # Deleting the copies is only half the job: the profile that produced them
    # would put every one back on the next sync. A --stale-only run leaves the
    # target alone, because that harness is still meant to receive skills.
    if removed and not stale_only:
        emptied = {os.path.realpath(item.target_root) for item in removed}
        forgotten = forget_targets(
            [loc for loc in locations if os.path.realpath(loc.path) in emptied],
            config=config_path,
        )
        for path in forgotten.deleted:
            console.print(
                f"[dim]Deleted {path}: no profile targets it any more.[/dim]"
            )
        for path in forgotten.updated:
            console.print(
                f"[dim]Dropped the uninstalled target from {path}.[/dim]"
            )


def _source_label(prof: SyncProfile) -> str:
    """What a profile reads from, for the header `doctor` prints."""
    if prof.auto_sources:
        return "auto (every other detected harness)"
    locs = prof.sources or ([prof.source] if prof.source else [])
    return ", ".join(loc.describe() for loc in locs)


def _doctor_all_profiles(
    profiles: list[SyncProfile],
    *,
    config_path: Path | None,
    strict: bool,
    fix: bool,
    as_json: bool,
    lock_timeout: float,
) -> None:
    """Check every profile in the config, counting each copy once.

    Repairing is per profile, because `sync` and `sync --prune` take a profile
    name; the re-check afterwards runs over all of them again, so the totals
    describe the profile as it is now rather than as it was.
    """
    before = diagnose_profiles(profiles)
    repairs: dict[str, RepairResult] = {}
    after = before
    if fix:
        for name, report in before.items():
            if not report.drift:
                continue
            repairs[name] = repair(
                report,
                profile_name=name,
                config_path=config_path,
                lock_wait=lock_timeout,
            )
        if repairs:
            after = diagnose_profiles(profiles)

    if as_json:
        click.echo(json.dumps(profiles_to_dict(after, repair=repairs or None), indent=2))
    else:
        by_name = {profile.name: profile for profile in profiles}
        for name, report in before.items():
            console.print(
                f"\n[bold]Profile:[/bold] {name}  "
                f"[bold]sources:[/bold] {_source_label(by_name[name])}"
            )
            _print_doctor(report)
            if report.reused_from:
                shared = ", ".join(sorted(report.reused_from))
                console.print(
                    f"[dim]Copies shared with {shared} were already checked "
                    "there.[/dim]"
                )
            if name in repairs:
                _print_repair(repairs[name], after[name])
        if len(before) > 1:
            checked = sum(report.checked for report in after.values())
            drift = sum(len(report.drift) for report in after.values())
            console.print(
                f"\n[bold]All profiles:[/bold] {checked} copy(ies) checked, "
                f"{drift} with drift"
            )

    if strict and any(report.drift for report in after.values()):
        raise SystemExit(1)


@main.command("doctor")
@click.option("--profile", default=None, help="Profile name from skill-bridge.yaml")
@click.option(
    "--all-profiles",
    "all_profiles",
    is_flag=True,
    help="Check every profile in the config, not just one",
)
@click.option("-c", "--config", "config_path", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--strict", is_flag=True, help="Exit 1 when a copy is stale or out of sync")
@click.option("--fix", is_flag=True, help="Sync out-of-sync copies and prune stale ones")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output for CI")
@click.option(
    "--schema",
    "show_schema",
    is_flag=True,
    help="Print the JSON Schema of the --json report and exit",
)
@click.option(
    "--lock-timeout",
    type=float,
    default=DEFAULT_LOCK_WAIT,
    show_default=True,
    help="With --fix: seconds to wait for another run holding the profile lock",
)
def doctor_cmd(
    profile: str | None,
    all_profiles: bool,
    config_path: Path | None,
    strict: bool,
    fix: bool,
    as_json: bool,
    show_schema: bool,
    lock_timeout: float,
) -> None:
    """Report stale, out-of-sync and lossy copies in one pass.

    Stale copies are the ones `sync --prune` removes, and out-of-sync copies no
    longer match the fingerprints their marker recorded, which `sync` refreshes;
    --strict exits 1 for either, so CI can gate on drift. Lossy fields are the
    frontmatter a target cannot express, which conversion parks in
    metadata.skill-bridge.extra: they are inherent to the format pair, so they
    are reported but never fail --strict.

    A copy whose marker records an older skill-bridge than the one running is
    `outdated`: a converter fix since then has not reached it, even though its
    contents can be exactly what its source says. That makes it information
    rather than drift, so --strict does not fail on it and --fix does not touch
    it; `sync` is what re-converts the copy and records the running version.

    --fix applies the two repairable findings (through the same guarded paths
    as `sync --prune` and `sync`), then checks the profile again. Copies edited
    after they were written are reported but never refreshed, and lossy
    findings are left alone because no conversion can restore a value the
    target format has no field for.

    --schema prints the JSON Schema of the --json document and exits, without
    reading a profile: it describes the format, so it has to work in a checkout
    that has not installed anything yet. The report carries report-version, and
    the schema is what that number refers to.

    --all-profiles checks every profile in the config instead of one, and counts
    each copy once even when two profiles name the same folder. --json then
    carries a `profiles` summary and tags each finding with its profile, while
    the totals cover the whole run; --strict and --fix apply to every profile.
    """
    if show_schema:
        click.echo(json.dumps(report_schema(), indent=2))
        return

    if all_profiles and profile:
        console.print("[red]--all-profiles checks every profile; drop --profile.[/red]")
        raise SystemExit(1)

    load_exporters()
    selected: list[SyncProfile] = []
    prof: SyncProfile | None = None
    try:
        config = load_config(config_path)
        if all_profiles:
            selected = list(config.profiles.values())
            if not selected:
                raise KeyError("This config has no profiles to check.")
        else:
            prof = config.get_profile(profile)
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else str(exc)
        console.print(f"[red]{message}[/red]")
        raise SystemExit(1) from exc
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    if prof is None:
        _doctor_all_profiles(
            selected,
            config_path=config_path,
            strict=strict,
            fix=fix,
            as_json=as_json,
            lock_timeout=lock_timeout,
        )
        return

    if not as_json:
        console.print(
            f"[bold]Profile:[/bold] {prof.name}  "
            f"[bold]sources:[/bold] {_source_label(prof)}"
        )

    report = diagnose(prof.targets)
    repair_result = None
    after = report
    if fix and report.drift:
        repair_result = repair(
            report,
            profile_name=profile,
            config_path=config_path,
            lock_wait=lock_timeout,
        )
        after = diagnose(prof.targets)

    if as_json:
        # Nothing but the document on stdout, so `| jq` and CI parsers work.
        click.echo(
            json.dumps(
                report_to_dict(after, profile=prof.name, repair=repair_result),
                indent=2,
            )
        )
    else:
        _print_doctor(report)
        if repair_result is not None:
            _print_repair(repair_result, after)

    if strict and after.drift:
        raise SystemExit(1)


@main.command("accept")
@click.option("--profile", default=None, help="Profile name from skill-bridge.yaml")
@click.option(
    "-c",
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
@click.option("--skill", "skill_name", default=None, help="Accept only this skill")
@click.option(
    "--lock-timeout",
    type=float,
    default=DEFAULT_LOCK_WAIT,
    show_default=True,
    help="Seconds to wait for another run holding the profile lock",
)
def accept_cmd(
    profile: str | None,
    config_path: Path | None,
    skill_name: str | None,
    lock_timeout: float,
) -> None:
    """Bless hand-edited copies so doctor stops reporting them as drift.

    `doctor` never overwrites a copy whose contents no longer match what it
    wrote, because that would throw away work. This is the other answer: keep
    the edit and make it the baseline. The marker's fingerprints are rewritten
    to what the copy and its source are now, and the copy is recorded as
    carrying local changes, so a later source edit is reported for a decision
    instead of being refreshed over the top of yours.
    """
    load_exporters()
    try:
        config = load_config(config_path)
        prof = config.get_profile(profile)
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else str(exc)
        console.print(f"[red]{message}[/red]")
        raise SystemExit(1) from exc
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    report = diagnose(prof.targets)
    result = accept_edits(
        report,
        profile_name=profile,
        config_path=config_path,
        skill_name=skill_name,
        lock_wait=lock_timeout,
    )
    if not result.accepted:
        console.print("Nothing to accept.")
        return

    for finding in result.accepted:
        console.print(
            f"{OK} accepted {finding.skill_name} "
            f"({finding.target_format.value}): {finding.skill_dir}"
        )
    console.print(f"\n[bold]Accepted {len(result.accepted)} copy(ies)[/bold]")


@main.command("annotate")
@click.option(
    "--report",
    default=None,
    help="doctor --json document to read, '-' for stdin (default: run the check)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["github", "sarif"]),
    default="github",
    show_default=True,
    help="github: workflow commands for CI logs; sarif: a SARIF 2.1.0 log",
)
@click.option("--profile", default=None, help="Profile name from skill-bridge.yaml")
@click.option(
    "-c",
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
def annotate_cmd(
    report: str | None,
    output_format: str,
    profile: str | None,
    config_path: Path | None,
) -> None:
    """Publish each finding to CI, as GitHub annotations or a SARIF log.

    Reads a `doctor --json` document with --report (or '-' for stdin), or runs
    the same check itself, and writes one workflow command per file that differs
    inside a copy: an error for drift a command can fix, a warning for a copy
    that needs a decision. Pointing at the file and line means a reviewer sees
    the annotation where the change belongs instead of on line 1, and a clean
    profile prints nothing at all.

    `--format sarif` writes the same findings as a SARIF 2.1.0 log for GitHub
    code scanning instead, one rule per kind and one result per file, so drift
    becomes an alert on the pull request and in the Security tab.

    A project that installs skill-bridge from pip can call this directly; it is
    the same writer our own CI uses.
    """
    if report is not None:
        try:
            raw = (
                sys.stdin.read()
                if report == "-"
                else Path(report).read_text(encoding="utf-8")
            )
            document = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise click.ClickException(f"not a doctor report: {exc}") from exc
    else:
        load_exporters()
        try:
            config = load_config(config_path)
            prof = config.get_profile(profile)
        except KeyError as exc:
            message = str(exc.args[0]) if exc.args else str(exc)
            console.print(f"[red]{message}[/red]")
            raise SystemExit(1) from exc
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/red]")
            raise SystemExit(1) from exc
        document = report_to_dict(diagnose(prof.targets), profile=prof.name)

    if output_format == "sarif":
        click.echo(json.dumps(sarif_log(document), indent=2))
        return
    for line in annotation_lines(document):
        click.echo(line)


@main.command("diff")
@click.option("--skill", "skill_name", default=None, help="Only this skill")
@click.option("--profile", default=None, help="Profile name from skill-bridge.yaml")
@click.option(
    "-c",
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
def diff_cmd(
    skill_name: str | None, profile: str | None, config_path: Path | None
) -> None:
    """Show what a sync would change, without writing anything.

    Converts each source into a temporary directory and diffs the result against
    the copy that is installed, so a reviewer sees the exact lines a sync would
    add, change or remove — including the hand-written text it would overwrite
    in a copy that was edited. Copies whose source is gone are left out, since
    `sync --prune` is the answer for those, and the exit code is 0: this is a
    view, and `doctor --strict` is the check. The one exception is a --skill
    name that matches no copy: that is a typo, not a clean profile, so it exits
    1 and says so.
    """
    load_exporters()
    try:
        config = load_config(config_path)
        prof = config.get_profile(profile)
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else str(exc)
        console.print(f"[red]{message}[/red]")
        raise SystemExit(1) from exc
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    report = diff_profile(prof.targets, skill_name=skill_name)
    for problem in report.problems:
        console.print(f"{WARN} {problem}")
    if not report.diffs:
        if skill_name is not None and report.checked == 0:
            # "nothing to change" would read as a clean result for a name that
            # simply is not there, which is the one thing this must not do.
            console.print(
                f"{WARN} no generated copy named '{skill_name}' in this profile."
            )
            raise SystemExit(1)
        console.print(
            f"{OK} {report.checked} copy(ies) checked, sync would change nothing."
        )
        return
    _print_diff(render_diff(report))
    console.print(
        f"\n[bold]{report.files} file(s) in {len(report.diffs)} copy(ies) "
        "would change.[/bold] sync applies them."
    )


@main.command("adopt")
@click.option("--to", "target_format", required=True, help="Harness to convert skills into")
@click.option("--from", "from_formats", multiple=True, help="Only import from these harnesses (repeatable)")
@click.option("--scope", type=click.Choice(["global", "project", "both"]), default="global")
@click.option("--to-path", type=click.Path(path_type=Path), default=None, help="Override the target skills directory")
@click.option("--force", is_flag=True, help="Overwrite native (hand-written) skills in the target")
@click.option("--dry-run", is_flag=True, help="Show what would be imported")
@click.option("--no-validate", is_flag=True, help="Skip validation warnings")
def adopt_cmd(
    target_format: str,
    from_formats: tuple[str, ...],
    scope: str,
    to_path: Path | None,
    force: bool,
    dry_run: bool,
    no_validate: bool,
) -> None:
    """Import skills from every other harness into one harness.

    Examples:

      skill-bridge adopt --to hermes

      skill-bridge adopt --to cursor --from claude-code --from codex
    """
    load_exporters()
    target = _parse_format(target_format)
    report = run_adopt(
        target,
        from_formats=[_parse_format(f) for f in from_formats] or None,
        scope=scope,
        target_path=to_path,
        force=force,
        validate=not no_validate,
        dry_run=dry_run,
    )
    console.print(f"[bold]Source harnesses scanned[/bold] {ARROW} {target.value}\n")
    _print_adopt(report, dry_run=dry_run)


@main.command("inspect")
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
def inspect_cmd(source: Path) -> None:
    """Show parsed metadata for a skill directory."""
    bundle = load_skill(source)
    console.print(f"[bold]Name:[/bold] {bundle.name}")
    console.print(f"[bold]Description:[/bold] {bundle.description[:200]}...")
    console.print(f"[bold]Detected source:[/bold] {bundle.source_format}")
    console.print(f"[bold]Supporting dirs:[/bold] {', '.join(bundle.supporting_dirs()) or '(none)'}")
    console.print(f"[bold]Body lines:[/bold] {len(bundle.body.splitlines())}")
    console.print(f"[bold]Converted by skill-bridge:[/bold] {is_bridge_generated(source)}")
    console.print("\n[bold]Frontmatter keys:[/bold]", ", ".join(bundle.frontmatter.keys()))


@main.command("validate")
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--harness", default=None, help="Also check what converting into this harness would lose")
@click.option("--fail-on-lossy", is_flag=True, help="Exit 1 when the --harness check finds losses")
def validate_cmd(source: Path, harness: str | None, fail_on_lossy: bool) -> None:
    """Validate a skill against the Agent Skills specification.

    With --harness, also report fields that harness cannot express natively
    (they are parked in metadata for a later hop, but this harness will not
    honor them). --fail-on-lossy turns those into a failure, e.g. for CI.
    """
    if fail_on_lossy and not harness:
        raise click.UsageError("--fail-on-lossy needs --harness")

    bundle = load_skill(source)
    report = validate_skill(bundle)
    if report.ok and not report.warnings():
        console.print(f"{OK} Skill is valid")
    else:
        for issue in report.issues:
            color = "red" if issue.level == "error" else "yellow"
            console.print(f"[{color}]{issue.level.upper()}[/] {issue.field}: {issue.message}")

    if harness:
        load_exporters()
        target = _parse_format(harness)
        lost = lossy_fields(bundle.frontmatter, target)
        for field in lost:
            console.print(
                f"[yellow]LOSSY[/] {field}: {target.value} cannot express this "
                "natively; it is parked in metadata.skill-bridge.extra for a "
                "later hop"
            )
        if lost:
            if fail_on_lossy:
                raise SystemExit(1)
        else:
            console.print(f"{OK} Nothing lost converting to {target.value}")

    if not report.ok:
        raise SystemExit(1)


@main.command("lint")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--harness", default=None, help="Format the copy was written for (default: its marker)")
@click.option("--matrix", "as_matrix", is_flag=True, help="Convert to every format and lint each result")
def lint_cmd(path: Path, harness: str | None, as_matrix: bool) -> None:
    """Check a converted copy against the harness it was written for.

    `validate` asks whether a skill is valid Agent Skills; this asks whether
    the output is what one harness reads. A field only another harness
    understands is reported instead of silently ignored, `allowed-tools` must
    have the shape that harness documents (a list for Devin, a string
    elsewhere), Hermes needs its `## When to Use` section, and anything parked
    in metadata.skill-bridge.extra must be something the harness cannot
    express.

    The format comes from the copy's provenance marker, or from --harness.
    --matrix converts the given skill into all 17 formats and lints each
    result, which is how the exporter matrix is checked end to end.
    """
    if as_matrix:
        reports = lint_matrix(path)
        failed = 0
        for fmt, report in reports.items():
            errors, warnings = len(report.errors()), len(report.warnings())
            state = "ok" if report.ok else "failed"
            console.print(
                f"{state:<7}{fmt.value:<14}{errors} error(s), {warnings} warning(s)"
            )
            for issue in report.issues:
                color = "red" if issue.level == "error" else "yellow"
                console.print(
                    f"  [{color}]{issue.level}[/] [{issue.rule}] {issue.message}"
                )
            failed += 0 if report.ok else 1
        console.print(
            f"\n[bold]{len(reports)} format(s) checked, {failed} failed.[/bold]"
        )
        if failed:
            raise SystemExit(1)
        return

    target = _lint_format(path, harness)
    report = lint_skill(path, target)
    for issue in report.issues:
        color = "red" if issue.level == "error" else "yellow"
        console.print(f"[{color}]{issue.level.upper()}[/] {issue.rule}: {issue.message}")
    if report.ok:
        console.print(f"{OK} {path.name} is what {target.value} expects")
    if not report.ok:
        raise SystemExit(1)


def _lint_format(path: Path, harness: str | None) -> AgentFormat:
    """The format to lint a copy against: --harness, or its marker."""
    if harness:
        return _parse_format(harness)
    recorded = read_marker(path).get("target-format")
    if recorded:
        try:
            return AgentFormat(str(recorded))
        except ValueError:
            pass
    console.print(
        "[red]No provenance marker here: pass --harness with the format "
        "this copy is for.[/red]"
    )
    raise SystemExit(1)


@main.command("convert")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--to",
    "targets",
    multiple=True,
    required=True,
    help="Target format(s): hermes, codex, cursor, claude-code, etc.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Output directory (default: ./converted-skills/<format>/...)",
)
@click.option("--no-validate", is_flag=True, help="Skip pre-conversion validation warnings")
def convert_cmd(
    source: Path,
    targets: tuple[str, ...],
    output: Path | None,
    no_validate: bool,
) -> None:
    """Convert one skill or a tree of skills to target agent format(s)."""
    load_exporters()
    formats = [_parse_format(t) for t in targets]

    if (source / "SKILL.md").is_file() or (source / "skill.md").is_file():
        skill_dirs = [source]
    else:
        skill_dirs = find_skills(source)
        if not skill_dirs:
            console.print(f"[red]No SKILL.md files found under {source}[/red]")
            raise SystemExit(1)

    base_out = output or Path.cwd() / "converted-skills"
    total = 0

    for fmt in formats:
        exporter = get_exporter(fmt)
        fmt_out = base_out if len(formats) == 1 else base_out / fmt.value
        fmt_out = fmt_out / exporter.default_output_subdir

        for skill_dir in skill_dirs:
            result = convert_skill(skill_dir, fmt, fmt_out, validate=not no_validate)
            total += 1
            console.print(
                f"{OK} {result.skill_name} {ARROW} {fmt.value} ({result.output_dir})"
            )
            for w in result.warnings:
                console.print(f"  {WARN} {w}")

    console.print(f"\n[bold]Converted {total} skill(s)[/bold] {ARROW} {base_out}")


@main.command("reverse")
@click.option("--from", "source_format", required=True, help="Source agent format (e.g. hermes, codex)")
@click.option("--to", "target_format", required=True, help="Target agent format (e.g. claude-code)")
@click.option("--from-path", type=click.Path(path_type=Path), default=None, help="Override source skills directory")
@click.option("--to-path", type=click.Path(path_type=Path), default=None, help="Override target skills directory")
@click.option("--scope", type=click.Choice(["global", "project"]), default="global")
@click.option("--skill", "skill_name", default=None, help="Convert a single skill by directory name")
@click.option("--no-validate", is_flag=True)
def reverse_cmd(
    source_format: str,
    target_format: str,
    from_path: Path | None,
    to_path: Path | None,
    scope: str,
    skill_name: str | None,
    no_validate: bool,
) -> None:
    """Reverse-convert skills between agent install directories.

    Examples:

      skill-bridge reverse --from hermes --to claude-code

      skill-bridge reverse --from codex --to cursor --scope project
    """
    src_fmt = _parse_format(source_format)
    tgt_fmt = _parse_format(target_format)

    src_root = resolve_skill_path(src_fmt, scope=scope, override=from_path)
    tgt_root = resolve_skill_path(tgt_fmt, scope=scope, override=to_path)

    console.print(f"[bold]Source[/bold] ({src_fmt.value}): {src_root}")
    console.print(f"[bold]Target[/bold] ({tgt_fmt.value}): {tgt_root}")

    if skill_name:
        skill_dir = src_root / skill_name
        if not (skill_dir / "SKILL.md").is_file():
            console.print(f"[red]Skill not found: {skill_dir}[/red]")
            raise SystemExit(1)
        results = [convert_skill(skill_dir, tgt_fmt, tgt_root, validate=not no_validate)]
    else:
        results = reverse_convert(
            src_fmt,
            tgt_fmt,
            source_path=from_path,
            target_path=to_path,
            scope=scope,
            validate=not no_validate,
        )

    _print_results(results)
    console.print(f"\n[bold]Reverse sync complete:[/bold] {len(results)} skill(s)")


@main.command("sync")
@click.option("--profile", default=None, help="Profile name from skill-bridge.yaml")
@click.option("-c", "--config", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--skill", "skill_name", default=None, help="Sync only one skill by name")
@click.option(
    "--prune",
    is_flag=True,
    help="Remove generated copies whose source was deleted or renamed",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="With --prune: report what would be removed, change nothing",
)
@click.option(
    "--lock-timeout",
    type=float,
    default=DEFAULT_LOCK_WAIT,
    show_default=True,
    help="Seconds to wait for another run holding the profile lock",
)
def sync_cmd(
    profile: str | None,
    config: Path | None,
    skill_name: str | None,
    prune: bool,
    dry_run: bool,
    lock_timeout: float,
) -> None:
    """Sync skills using skill-bridge.yaml profile (sources -> all targets)."""
    if dry_run and not prune:
        raise click.UsageError("--dry-run only applies together with --prune")

    try:
        cfg = load_config(config)
        prof = cfg.get_profile(profile)
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else str(exc)
        console.print(f"[red]{message}[/red]")
        raise SystemExit(1) from exc
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    if prof.auto_sources:
        source_label = "auto (every other detected harness)"
    else:
        locs = prof.sources or ([prof.source] if prof.source else [])
        source_label = ", ".join(loc.describe() for loc in locs)
    console.print(f"[bold]Profile:[/bold] {prof.name}  [bold]sources:[/bold] {source_label}")

    results = []
    if dry_run:
        console.print("[dim]Dry run: sync skipped, only pruning is reported.[/dim]")
    else:
        try:
            results = run_sync(
                profile_name=profile,
                config_path=config,
                skill_name=skill_name,
                lock_wait=lock_timeout,
            )
        except (FileNotFoundError, RemoteError) as exc:
            if not prune or isinstance(exc, RemoteError):
                console.print(f"[red]{exc}[/red]")
                raise SystemExit(1) from exc
            # Nothing left to import is exactly when pruning matters most.
            console.print(f"{WARN} {exc}")
        _print_results(results)
        console.print(f"\n[bold]Synced {len(results)} conversion(s)[/bold]")

    if not prune:
        return

    try:
        outcome = run_prune(
            profile_name=profile,
            config_path=config,
            skill_name=skill_name,
            dry_run=dry_run,
            lock_wait=lock_timeout,
        )
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else str(exc)
        console.print(f"[red]{message}[/red]")
        raise SystemExit(1) from exc
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    _print_removals(
        outcome.plan,
        outcome.removed,
        dry_run=dry_run,
        action="prune",
        dry_run_action="would prune",
    )


@main.command("fetch")
@click.option("--profile", default=None, help="Profile name from skill-bridge.yaml")
@click.option("-c", "--config", type=click.Path(exists=True, path_type=Path), default=None)
@click.option(
    "--refresh",
    is_flag=True,
    help="Fetch each remote's current commit instead of reusing the checkout",
)
def fetch_cmd(profile: str | None, config: Path | None, refresh: bool) -> None:
    """Check out the profile's git sources and print where they landed.

    `sync` does this itself; the command exists to make the checkout
    discoverable — which copy of a skill a run reads, and where to look when a
    ref stops resolving.
    """
    try:
        cfg = load_config(config)
        prof = cfg.get_profile(profile)
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else str(exc)
        console.print(f"[red]{message}[/red]")
        raise SystemExit(1) from exc
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    locs = prof.sources or ([prof.source] if prof.source else [])
    git_locs = [loc for loc in locs if loc.git is not None]
    if not git_locs:
        console.print(f"[dim]Profile '{prof.name}' has no git sources.[/dim]")
        return

    for loc in git_locs:
        assert loc.git is not None
        try:
            path = checkout(loc.git, refresh=refresh)
        except RemoteError as exc:
            console.print(f"[red]{loc.git.label}[/red]")
            console.print(f"  {exc}")
            raise SystemExit(1) from exc
        console.print(f"{loc.format.value:<14} {loc.git.label}")
        console.print(f"  {ARROW} {path}")

    console.print(f"\n[bold]{len(git_locs)} git source(s) ready[/bold]")


@main.command("watch")
@click.option("--profile", default=None, help="Profile name from skill-bridge.yaml")
@click.option("-c", "--config", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--debounce", default=1.0, type=float, help="Seconds to debounce file changes")
def watch_cmd(profile: str | None, config: Path | None, debounce: float) -> None:
    """Watch source skills and re-sync to targets on change (Ctrl+C to stop)."""
    try:
        cfg = load_config(config)
        prof = cfg.get_profile(profile)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    roots = watch_roots(prof)
    label = "auto-detected harnesses" if prof.auto_sources else "sources"
    if roots:
        console.print(f"[bold]Watching[/bold] {label}:")
        for root in roots:
            console.print(f"  {root}")
    else:
        console.print(
            f"[bold]Watching[/bold] {label}: "
            "[yellow]none found yet, waiting for one to appear[/yellow]"
        )
    console.print(f"[bold]Targets:[/bold] {', '.join(t.format.value for t in prof.targets)}")
    console.print("Press Ctrl+C to stop.\n")

    def on_sync(reason, results):
        console.print(f"\n[cyan]Sync triggered[/cyan] ({reason})")
        _print_results(results, show_path=False)

    def on_error(exc):
        console.print(f"[red]Sync failed:[/red] {exc}")

    try:
        watch_from_config(
            profile_name=profile,
            config_path=config,
            debounce_seconds=debounce,
            on_sync=on_sync,
            on_error=on_error,
        )
    except (ImportError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc


@main.command("batch")
@click.argument("source_root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--all-agents", is_flag=True, help="Convert to every supported agent format")
@click.option("--to", "targets", multiple=True, help="Target format(s) when not using --all-agents")
@click.option("-o", "--output", type=click.Path(file_okay=False, path_type=Path), default=None)
def batch_cmd(
    source_root: Path,
    all_agents: bool,
    targets: tuple[str, ...],
    output: Path | None,
) -> None:
    """Batch-convert every skill under a directory tree."""
    load_exporters()
    if all_agents:
        formats = [e.format_id for e in all_exporters()]
    elif targets:
        formats = [_parse_format(t) for t in targets]
    else:
        console.print("[red]Specify --all-agents or one or more --to formats[/red]")
        raise SystemExit(1)

    results = convert_batch(source_root, formats, output)
    _print_results(results)
    console.print(f"\n[bold]Done:[/bold] {len(results)} conversions")


@main.command("paths")
@click.argument("format_name")
@click.option("--scope", type=click.Choice(["global", "project", "both"]), default="global")
def paths_cmd(format_name: str, scope: str) -> None:
    """Print the default install path for an agent format."""
    fmt = _parse_format(format_name)
    load_exporters()
    if scope == "both":
        for name in ("global", "project"):
            console.print(f"{name}: {resolve_skill_path(fmt, scope=name)}")
        return
    console.print(str(resolve_skill_path(fmt, scope=scope)))


if __name__ == "__main__":
    main()
