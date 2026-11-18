"""Skill Bridge CLI."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from skill_bridge.agent_paths import detect_installed_agents, global_skill_root, resolve_skill_path
from skill_bridge.config import init_config_interactive, load_config, save_config
from skill_bridge.converter import convert_batch, convert_skill, default_output_root
from skill_bridge.formats.base import all_exporters, load_exporters
from skill_bridge.models import AgentFormat
from skill_bridge.parser import find_skills, load_skill
from skill_bridge.sync import reverse_convert, run_sync
from skill_bridge.validator import validate_skill
from skill_bridge.watch import watch_from_config

console = Console()
OK = "[green]OK[/green]"
WARN = "[yellow]![/yellow]"
ARROW = "->"


def _print_results(results, *, show_path: bool = True) -> None:
    for r in results:
        loc = f" ({r.output_dir})" if show_path else ""
        console.print(f"{OK} {r.skill_name} {ARROW} {r.target_format.value}{loc}")
        for w in r.warnings:
            console.print(f"  {WARN} {w}")


@click.group()
@click.version_option(package_name="skill-bridge", prog_name="skill-bridge")
def main() -> None:
    """Convert and sync agent skills across Claude Code, Hermes, Codex, Cursor, and more."""


@main.command("list-formats")
def list_formats_cmd() -> None:
    """List supported target agent formats."""
    load_exporters()
    table = Table(title="Supported Agent Formats")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Default output path")
    table.add_column("Description")

    for exporter in sorted(all_exporters(), key=lambda e: e.format_id.value):
        table.add_row(
            exporter.format_id.value,
            exporter.display_name,
            exporter.default_output_subdir,
            exporter.description,
        )
    console.print(table)


@main.command("detect")
def detect_cmd() -> None:
    """Show which agent skill directories exist on this machine."""
    load_exporters()
    installed = detect_installed_agents()
    if not installed:
        console.print("[yellow]No agent skill directories found yet.[/yellow]")
        console.print("Typical locations:")
        for fmt in [AgentFormat.CLAUDE_CODE, AgentFormat.HERMES, AgentFormat.CODEX, AgentFormat.CURSOR]:
            console.print(f"  {fmt.value}: {global_skill_root(fmt)}")
        return
    table = Table(title="Detected Agent Skill Directories")
    table.add_column("Format", style="cyan")
    table.add_column("Path")
    for fmt, path in sorted(installed.items(), key=lambda x: x[0].value):
        count = len(find_skills(path)) if path.is_dir() else 0
        table.add_row(fmt.value, f"{path}  ({count} skills)")
    console.print(table)


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
    out_path = output or Path.cwd() / "skill-bridge.yaml"
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
    console.print("  skill-bridge reverse --from hermes --to claude-code")


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
        exporter = next(e for e in all_exporters() if e.format_id == fmt)
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
    """Sync skills using skill-bridge.yaml profile (source -> all targets)."""
    try:
        results = run_sync(profile_name=profile, config_path=config, skill_name=skill_name)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    cfg = load_config(config)
    prof = cfg.get_profile(profile)
    console.print(f"[bold]Profile:[/bold] {prof.name}  [bold]source:[/bold] {prof.source.path}")
    _print_results(results)
    console.print(f"\n[bold]Synced {len(results)} conversion(s)[/bold]")


@main.command("watch")
@click.option("--profile", default=None, help="Profile name from skill-bridge.yaml")
@click.option("-c", "--config", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--debounce", default=1.0, type=float, help="Seconds to debounce file changes")
def watch_cmd(profile: str | None, config: Path | None, debounce: float) -> None:
    """Watch source skills and re-sync to targets on change (Ctrl+C to stop).

    Works with any configured profile, including reverse-sync profiles that
    set watch: false.
    """
    try:
        cfg = load_config(config)
        prof = cfg.get_profile(profile)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    except KeyError as exc:
        console.print(f"[red]{exc.args[0] if exc.args else exc}[/red]")
        raise SystemExit(1) from exc

    if not prof.watch:
        console.print(
            f"[yellow]Profile '{prof.name}' has watch: false; watching anyway "
            "because watch was requested explicitly.[/yellow]"
        )
    console.print(f"[bold]Watching[/bold] {prof.source.path}")
    console.print(f"[bold]Targets:[/bold] {', '.join(t.format.value for t in prof.targets)}")
    console.print("Press Ctrl+C to stop.\n")

    def on_sync(reason, results):
        if not results:
            console.print(
                f"\n[cyan]No skills to sync yet[/cyan] in {prof.source.path}"
            )
            if reason == "initial":
                console.print(
                    "Waiting for SKILL.md files to appear (Ctrl+C to stop).\n"
                )
            return
        if reason == "initial":
            console.print(f"\n[cyan]Initial sync complete[/cyan] ({len(results)} conversion(s))")
        else:
            console.print(f"\n[cyan]Sync triggered[/cyan] ({reason})")
        _print_results(results, show_path=False)

    def on_error(reason, exc):
        console.print(f"[red]Sync failed[/red] ({reason}): {exc}")

    try:
        watch_from_config(
            profile_name=profile,
            config_path=config,
            debounce_seconds=debounce,
            on_sync=on_sync,
            on_error=on_error,
            force=True,
        )
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    except FileNotFoundError as exc:
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
@click.option("--scope", type=click.Choice(["global", "project"]), default="global")
def paths_cmd(format_name: str, scope: str) -> None:
    """Print the default install path for an agent format."""
    fmt = AgentFormat.from_string(format_name)
    load_exporters()
    out = resolve_skill_path(fmt, scope=scope) if scope == "global" else default_output_root(fmt)
    console.print(str(out))


if __name__ == "__main__":
    main()
