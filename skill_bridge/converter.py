"""Orchestrate skill conversion across formats."""

from __future__ import annotations

from pathlib import Path

from skill_bridge.formats.base import get_exporter, load_exporters
from skill_bridge.models import AgentFormat, ConversionResult
from skill_bridge.parser import find_skills, load_skill
from skill_bridge.provenance import write_marker
from skill_bridge.validator import validate_skill


def default_output_root(fmt: AgentFormat, base: Path | None = None) -> Path:
    load_exporters()
    exporter = get_exporter(fmt)
    root = base or Path.cwd()
    return root / exporter.default_output_subdir


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

    exporter = get_exporter(target_format)
    out = output_root or default_output_root(target_format)
    result = exporter.export(bundle, out)
    write_marker(
        result.output_dir,
        source_format=bundle.source_format,
        target_format=target_format,
        source_path=bundle.root,
    )
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
