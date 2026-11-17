"""Skill Bridge CLI."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from skill_bridge.adopt import AdoptReport, run_adopt
from skill_bridge.agent_paths import detect_installed_agents, global_skill_root, resolve_skill_path
from skill_bridge.config import (
    DEFAULT_CONFIG_NAME,
    auto_import_config,
    init_config_interactive,
    load_config,
    save_config,
)
from skill_bridge.converter import convert_batch, convert_skill
from skill_bridge.formats.base import all_exporters, get_exporter, load_exporters
from skill_bridge.models import AgentFormat
from skill_bridge.parser import find_skills, load_skill
from skill_bridge.provenance import is_bridge_generated
from skill_bridge.sync import reverse_convert, run_sync
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


def _print_results(results, *, show_path: bool = True) -> None:
    for r in results:
        loc = f" ({r.output_dir})" if show_path else ""
        console.print(f"{OK} {r.skill_name} {ARROW} {r.target_format.value}{loc}")
        for w in r.warnings:
            console.print(f"  {WARN} {w}")


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


@click.group()
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
def detect_cmd() -> None:
    """Show every supported harness and whether its skill directory exists here."""
    load_exporters()
    installed = detect_installed_agents()
    counts: dict[str, int] = {}

    table = Table(title="Agent harnesses on this machine")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Global skill directory")
    table.add_column("Status")
    table.add_column("Skills", justify="right")

    for exporter in sorted(all_exporters(), key=lambda e: e.format_id.value):
        fmt = exporter.format_id
        if fmt is AgentFormat.GENERIC:
            continue
        path = global_skill_root(fmt)
        found = fmt in installed
        if found:
            key = str(path)
            if key not in counts:
                counts[key] = len(find_skills(path))
            count = str(counts[key])
        else:
            count = "-"
        table.add_row(
            fmt.value,
            exporter.display_name,
            str(path),
            "[green]detected[/green]" if found else "[dim]not found[/dim]",
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

    src = AgentFormat.from_string(source_format)
    tgts = [AgentFormat.from_string(t) for t in targets]
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


def _resolve_install_target(harness: str | None, config_path: Path | None) -> AgentFormat:
    if harness:
        return AgentFormat.from_string(harness)

    detected = detect_installed_agents()
    if len(detected) == 1:
        return next(iter(detected))

    for candidate in (config_path, Path.cwd() / DEFAULT_CONFIG_NAME):
        if candidate and candidate.is_file():
            try:
                profile = load_config(candidate).get_profile(None)
            except (FileNotFoundError, KeyError, ValueError):
                continue
            if profile.targets:
                return profile.targets[0].format

    listing = (
        "\n".join(
            f"  {fmt.value:<12} {path}"
            for fmt, path in sorted(detected.items(), key=lambda kv: kv[0].value)
        )
        or "  (none found)"
    )
    console.print("[red]Could not tell which harness you are installing into.[/red]")
    console.print("Harness skill directories found:\n" + listing)
    console.print("\nRe-run with the harness you use, e.g. skill-bridge install --harness hermes")
    raise SystemExit(1)


@main.command("install")
@click.option("-H", "--harness", default=None, help="Harness you are installing into (e.g. hermes, cursor)")
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
    in yours. Writes an 'auto-import' profile so `skill-bridge sync` keeps them
    up to date afterwards.
    """
    load_exporters()
    target = _resolve_install_target(harness, config_path)
    target_root = resolve_skill_path(target, override=to_path)

    console.print(f"[bold]Harness:[/bold] {target.value}")
    console.print(f"[bold]Skills go to:[/bold] {target_root}")

    sources = [AgentFormat.from_string(f) for f in from_formats]
    cfg_out = config_path or Path.cwd() / DEFAULT_CONFIG_NAME
    if dry_run:
        console.print(f"[dim]Dry run: would write {cfg_out}[/dim]")
    elif cfg_out.exists() and not force:
        console.print(f"[dim]Keeping existing {cfg_out}[/dim]")
    else:
        cfg = auto_import_config(target, from_formats=sources or None)
        saved = save_config(cfg, cfg_out)
        console.print(f"{OK} Wrote {saved} (profile 'auto-import')")

    if no_sync:
        console.print("\nNext: skill-bridge sync")
        return

    report = run_adopt(
        target,
        from_formats=sources or None,
        scope=scope,
        target_path=to_path,
        force=force,
        validate=not no_validate,
        dry_run=dry_run,
    )
    _print_adopt(report, dry_run=dry_run)
    if not dry_run:
        console.print("\nKeep it current: [cyan]skill-bridge sync[/cyan] or [cyan]skill-bridge watch[/cyan]")


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
    target = AgentFormat.from_string(target_format)
    report = run_adopt(
        target,
        from_formats=[AgentFormat.from_string(f) for f in from_formats] or None,
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
def validate_cmd(source: Path) -> None:
    """Validate a skill against the Agent Skills specification."""
    bundle = load_skill(source)
    report = validate_skill(bundle)
    if report.ok and not report.warnings():
        console.print(f"{OK} Skill is valid")
        return
    for issue in report.issues:
        color = "red" if issue.level == "error" else "yellow"
        console.print(f"[{color}]{issue.level.upper()}[/] {issue.field}: {issue.message}")
    if not report.ok:
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
    formats = [AgentFormat.from_string(t) for t in targets]

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
    src_fmt = AgentFormat.from_string(source_format)
    tgt_fmt = AgentFormat.from_string(target_format)

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
def sync_cmd(profile: str | None, config: Path | None, skill_name: str | None) -> None:
    """Sync skills using skill-bridge.yaml profile (sources -> all targets)."""
    try:
        results = run_sync(profile_name=profile, config_path=config, skill_name=skill_name)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    cfg = load_config(config)
    prof = cfg.get_profile(profile)
    if prof.auto_sources:
        source_label = "auto (every other detected harness)"
    else:
        locs = prof.sources or ([prof.source] if prof.source else [])
        source_label = ", ".join(f"{loc.format.value}:{loc.path}" for loc in locs)
    console.print(f"[bold]Profile:[/bold] {prof.name}  [bold]sources:[/bold] {source_label}")
    _print_results(results)
    console.print(f"\n[bold]Synced {len(results)} conversion(s)[/bold]")


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
        formats = [AgentFormat.from_string(t) for t in targets]
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
    fmt = AgentFormat.from_string(format_name)
    load_exporters()
    if scope == "both":
        for name in ("global", "project"):
            console.print(f"{name}: {resolve_skill_path(fmt, scope=name)}")
        return
    console.print(str(resolve_skill_path(fmt, scope=scope)))


if __name__ == "__main__":
    main()
