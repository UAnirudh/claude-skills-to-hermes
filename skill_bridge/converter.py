"""Orchestrate skill conversion across formats."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from skill_bridge.formats.base import get_exporter, load_exporters
from skill_bridge.models import AgentFormat, ConversionResult
from skill_bridge.parser import find_skills, load_skill
from skill_bridge.provenance import tree_hash, write_marker
from skill_bridge.validator import validate_skill


def default_output_root(fmt: AgentFormat, base: Path | None = None) -> Path:
    load_exporters()
    exporter = get_exporter(fmt)
    root = base or Path.cwd()
    return root / exporter.default_output_subdir


def _swap_into_place(staged: Path, dest: Path, *, holder: Path) -> None:
    """Move ``staged`` onto ``dest`` without ever exposing a partial copy.

    ``os.replace`` cannot put a directory on top of a non-empty one, so an
    existing copy is renamed into ``holder`` first — outside the skills root,
    so nothing scanning for skills can see it. The destination is then absent
    for an instant, which a concurrent sync reads as "not there yet", never as
    a half-written skill. If the second rename fails, the old copy goes back.
    """
    previous: Path | None = None
    if dest.exists() or dest.is_symlink():
        previous = holder / "previous"
        os.replace(dest, previous)
    try:
        os.replace(staged, dest)
    except OSError:
        if previous is not None and not dest.exists():
            os.replace(previous, dest)
        raise


def convert_skill(
    source: Path,
    target_format: AgentFormat,
    output_root: Path | None = None,
    *,
    validate: bool = True,
) -> ConversionResult:
    load_exporters()
    bundle = load_skill(source)
    warnings: list[str] = []

    if validate:
        report = validate_skill(bundle, strict_dir_name=False)
        warnings.extend(f"[{i.level}] {i.field}: {i.message}" for i in report.issues)

    # Fingerprint the source before exporting, so `doctor` can tell a copy is
    # behind without converting anything again.
    source_hash = tree_hash(bundle.root)
    exporter = get_exporter(target_format)
    out = Path(output_root or default_output_root(target_format))
    out.mkdir(parents=True, exist_ok=True)
    # Stage beside the skills root: same filesystem, so the final rename is
    # atomic, but outside the directories sync and doctor scan, so an
    # interrupted run cannot leave a half-written skill looking like a real
    # one. The marker is written while staged, so it arrives with the copy.
    holder = Path(tempfile.mkdtemp(prefix=".skill-bridge-tmp-", dir=out.parent))
    try:
        result = exporter.export(bundle, holder)
        write_marker(
            result.output_dir,
            source_format=bundle.source_format,
            target_format=target_format,
            source_path=bundle.root,
            source_hash=source_hash,
            output_hash=tree_hash(result.output_dir),
        )
        final_dir = out / Path(result.output_dir).relative_to(holder)
        _swap_into_place(Path(result.output_dir), final_dir, holder=holder)
    finally:
        shutil.rmtree(holder, ignore_errors=True)

    result.output_dir = final_dir
    result.warnings = warnings + result.warnings
    return result


def convert_batch(
    source_root: Path,
    target_formats: list[AgentFormat],
    output_base: Path | None = None,
    *,
    validate: bool = True,
) -> list[ConversionResult]:
    load_exporters()
    skills = find_skills(source_root)
    if not skills:
        raise FileNotFoundError(f"No skills found under {source_root}")

    results: list[ConversionResult] = []
    base = output_base or Path.cwd() / "converted-skills"

    for skill_dir in skills:
        for fmt in target_formats:
            out = base / fmt.value / get_exporter(fmt).default_output_subdir
            results.append(convert_skill(skill_dir, fmt, out, validate=validate))
    return results
