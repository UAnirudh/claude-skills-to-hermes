"""Format exporter registry and base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle


class FormatExporter(ABC):
    format_id: AgentFormat
    display_name: str
    default_output_subdir: str
    description: str
    # Global install root when it differs from the conversion output.
    global_output_subdir: str | None = None

    @abstractmethod
    def export(
        self,
        bundle: SkillBundle,
        output_root: Path,
        *,
        skill_name: str | None = None,
    ) -> ConversionResult:
        ...


_EXPORTERS: dict[AgentFormat, FormatExporter] = {}


def register_exporter(exporter: FormatExporter) -> None:
    _EXPORTERS[exporter.format_id] = exporter


def get_exporter(fmt: AgentFormat) -> FormatExporter:
    if fmt not in _EXPORTERS:
        raise KeyError(f"No exporter registered for {fmt.value}")
    return _EXPORTERS[fmt]


def all_exporters() -> list[FormatExporter]:
    return list(_EXPORTERS.values())


def load_exporters() -> None:
    """Import all exporter modules to populate registry."""
    from skill_bridge.formats import (  # noqa: F401
        claude_code,
        cline,
        codex,
        continue_dev,
        cursor,
        generic,
        hermes,
        openagents,
        openclaw,
        opencode,
        roo,
        windsurf,
    )

    _ = (
        claude_code,
        cursor,
        hermes,
        codex,
        openagents,
        windsurf,
        cline,
        roo,
        continue_dev,
        opencode,
        openclaw,
        generic,
    )
