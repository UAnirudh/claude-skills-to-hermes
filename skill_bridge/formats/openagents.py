"""agentskills.io standard exporter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skill_bridge.compat import adapt_frontmatter
from skill_bridge.formats.base import FormatExporter, register_exporter
from skill_bridge.models import AgentFormat, ConversionResult, SkillBundle
from skill_bridge.utils import (
    copy_skill_tree,
    normalize_reference_dirs,
    render_skill_md,
    slugify_name,
)


def build_standard_frontmatter(
    bundle: SkillBundle,
    name: str,
    *,
    target: AgentFormat | None = None,
) -> dict[str, Any]:
    """Frontmatter for ``target``, translated to the fields that harness reads.

    Every exporter builds its output through here, so a skill authored for one
    harness and converted to any other carries that harness's native fields
    (Cursor's ``disable-model-invocation``, Devin's ``allowed-tools`` list,
    Hermes' ``metadata.hermes`` category, …) instead of a blind copy of the
    source's keys.
    """
    return adapt_frontmatter(
        bundle, name, target=target or AgentFormat.OPENAGENTS
    )


class OpenAgentsExporter(FormatExporter):
    format_id = AgentFormat.OPENAGENTS
    display_name = "Agent Skills (agentskills.io)"
    default_output_subdir = "skills"
    description = "Open standard format adopted by Claude Code, Codex, Hermes, and others"

    def export(
        self,
        bundle: SkillBundle,
        output_root: Path,
        *,
        skill_name: str | None = None,
    ) -> ConversionResult:
        name = slugify_name(skill_name or bundle.name)
        dest = output_root / name
        if dest.exists():
            import shutil

            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        copy_skill_tree(bundle.root, dest)
        warnings = normalize_reference_dirs(bundle, dest)
        fm = build_standard_frontmatter(bundle, name, target=self.format_id)
        (dest / "SKILL.md").write_text(render_skill_md(fm, bundle.body), encoding="utf-8")
        return ConversionResult(bundle.root, self.format_id, dest, name, warnings)


register_exporter(OpenAgentsExporter())
