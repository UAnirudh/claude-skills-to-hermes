"""Check a converted copy against the harness it was written for.

`validate` asks whether a skill is a valid Agent Skill. This asks a different
question: is the output an exporter wrote something that harness actually
reads? A harness ignores keys it does not know, and some frontends reject a
skill outright instead, so a field only another harness understands must not
sit at the top level. ``allowed-tools`` is a space-separated string everywhere
except Devin, which documents a list; Hermes expects its ``## When to Use``
section and its ``metadata.hermes`` block; reference material belongs in
``references/``, never ``reference/``; and anything parked in
``metadata.skill-bridge.extra`` must be something the harness cannot express.

The rules are per format, in :data:`SPECS`, built from the same table
:mod:`skill_bridge.compat` uses to translate fields — so the lint and the
conversion cannot drift apart. The test suite converts the example skill into
all 17 formats and lints every result.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from skill_bridge.compat import CARRIER, native_fields
from skill_bridge.converter import convert_skill
from skill_bridge.formats.base import load_exporters
from skill_bridge.models import AgentFormat
from skill_bridge.parser import parse_frontmatter
from skill_bridge.provenance import is_bridge_generated, read_marker
from skill_bridge.validator import NAME_RE

SKILL_FILE = "SKILL.md"
MAX_NAME = 64
MAX_DESCRIPTION = 1024

#: Prefix of a staging directory a killed conversion may have left behind.
STAGING_PREFIX = ".skill-bridge-tmp-"

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class LintIssue:
    """One way a copy fails the format it was written for."""

    level: str
    rule: str
    message: str


@dataclass
class LintReport:
    """Everything one copy got wrong, for one harness."""

    skill_dir: Path
    target_format: AgentFormat
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.level == ERROR for issue in self.issues)

    def errors(self) -> list[LintIssue]:
        return [issue for issue in self.issues if issue.level == ERROR]

    def warnings(self) -> list[LintIssue]:
        return [issue for issue in self.issues if issue.level == WARNING]


@dataclass(frozen=True)
class FormatSpec:
    """What one harness reads in a SKILL.md, and how it must be written."""

    #: Top-level keys the harness documents reading, beyond name/description.
    fields: frozenset[str]
    #: ``allowed-tools`` is a YAML list for this harness, not a string.
    allowed_tools_list: bool = False
    #: Body headings the exporter promises to add; any one of them will do.
    body_sections: tuple[str, ...] = ()
    #: Metadata namespace the harness reads (Hermes' ``metadata.hermes``).
    metadata_block: str | None = None


#: One entry per format, derived from the field table the conversion uses.
SPECS: dict[AgentFormat, FormatSpec] = {
    fmt: FormatSpec(fields=native_fields(fmt)) for fmt in AgentFormat
}
SPECS[AgentFormat.DEVIN] = FormatSpec(
    fields=native_fields(AgentFormat.DEVIN), allowed_tools_list=True
)
SPECS[AgentFormat.HERMES] = FormatSpec(
    fields=native_fields(AgentFormat.HERMES),
    body_sections=("## when to use", "## when to activate"),
    metadata_block="hermes",
)


def _error(rule: str, message: str) -> LintIssue:
    return LintIssue(ERROR, rule, message)


def _warn(rule: str, message: str) -> LintIssue:
    return LintIssue(WARNING, rule, message)


def lint_skill(skill_dir: Path, target: AgentFormat) -> LintReport:
    """Check one copy against the format it claims to be written for."""
    skill_dir = Path(skill_dir)
    report = LintReport(skill_dir=skill_dir, target_format=target)
    spec = SPECS[target]
    path = skill_dir / SKILL_FILE

    if not path.is_file():
        report.issues.append(_error("layout", f"{SKILL_FILE} is missing"))
        return report
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.issues.append(_error("layout", f"{SKILL_FILE} cannot be read ({exc})"))
        return report
    if not text.startswith("---"):
        report.issues.append(
            _error("frontmatter", f"{SKILL_FILE} must open with a --- block")
        )
        return report
    try:
        frontmatter, body = parse_frontmatter(text)
    except (ValueError, yaml.YAMLError) as exc:
        report.issues.append(
            _error("frontmatter", f"frontmatter cannot be parsed ({exc})")
        )
        return report
    if not frontmatter:
        report.issues.append(_error("frontmatter", "frontmatter is empty"))
        return report

    _check_identity(skill_dir, frontmatter, report)
    _check_fields(frontmatter, spec, report)
    _check_types(frontmatter, spec, report, is_bridge_generated(skill_dir))
    _check_carrier(frontmatter, spec, report)
    _check_body(body, spec, report)
    _check_layout(skill_dir, report)
    _check_marker(skill_dir, target, report)
    return report


def _check_identity(
    skill_dir: Path, frontmatter: dict[str, object], report: LintReport
) -> None:
    name = frontmatter.get("name")
    if not isinstance(name, str) or not name.strip():
        report.issues.append(_error("name", "name is required"))
    elif len(name) > MAX_NAME:
        report.issues.append(
            _error("name", f"name must be at most {MAX_NAME} characters")
        )
    elif not NAME_RE.match(name):
        report.issues.append(
            _error(
                "name",
                "name must be lowercase alphanumeric with single hyphens",
            )
        )
    elif name != skill_dir.name:
        report.issues.append(
            _warn(
                "name",
                f"the folder is '{skill_dir.name}' but the frontmatter says '{name}'",
            )
        )

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        report.issues.append(_error("description", "description is required"))
    elif len(description) > MAX_DESCRIPTION:
        report.issues.append(
            _error(
                "description",
                f"description must be at most {MAX_DESCRIPTION} characters",
            )
        )


def _check_fields(
    frontmatter: dict[str, object], spec: FormatSpec, report: LintReport
) -> None:
    readable = spec.fields | {"name", "description"}
    foreign = sorted(key for key in frontmatter if key not in readable)
    if foreign:
        report.issues.append(
            _error(
                "fields",
                f"{report.target_format.value} does not read "
                f"{', '.join(foreign)}; that belongs in "
                f"metadata.{CARRIER}.extra",
            )
        )


def _check_types(
    frontmatter: dict[str, object],
    spec: FormatSpec,
    report: LintReport,
    generated: bool,
) -> None:
    fmt = report.target_format.value
    tools = frontmatter.get("allowed-tools")
    if tools is not None:
        if spec.allowed_tools_list:
            if not isinstance(tools, list) or any(
                not isinstance(tool, str) for tool in tools
            ):
                report.issues.append(
                    _error("allowed-tools", f"{fmt} documents a YAML list of tools")
                )
        elif not isinstance(tools, str):
            report.issues.append(
                _error(
                    "allowed-tools",
                    f"{fmt} documents a space-separated string of tools",
                )
            )

    for key in ("disable-model-invocation", "user-invocable"):
        if key in frontmatter and not isinstance(frontmatter[key], bool):
            report.issues.append(_error(key, "must be true or false"))

    if "paths" in spec.fields:
        paths = frontmatter.get("paths")
        if paths is not None and not isinstance(paths, list):
            report.issues.append(_error("paths", "must be a list of globs"))

    if "triggers" in spec.fields:
        triggers = frontmatter.get("triggers")
        if triggers is not None and not isinstance(triggers, list):
            report.issues.append(_error("triggers", "must be a list"))

    platforms = frontmatter.get("platforms")
    if platforms is not None:
        allowed = {"macos", "linux", "windows"}
        if not isinstance(platforms, list) or any(
            str(platform) not in allowed for platform in platforms
        ):
            report.issues.append(
                _error("platforms", "must list macos, linux or windows")
            )

    meta = frontmatter.get("metadata")
    if meta is not None and not isinstance(meta, dict):
        report.issues.append(_error("metadata", "must be a mapping"))
    elif spec.metadata_block:
        block = meta.get(spec.metadata_block) if isinstance(meta, dict) else None
        if not isinstance(block, dict):
            message = f"metadata.{spec.metadata_block} block is missing"
            # The block is the exporter's own convention, so a copy it wrote
            # without one is a bug; a hand-written skill simply may not have it.
            report.issues.append(
                _error("metadata", message) if generated else _warn("metadata", message)
            )
        elif spec.metadata_block == "hermes":
            tags = block.get("tags")
            if tags is not None and not isinstance(tags, list):
                report.issues.append(_error("metadata.hermes", "tags must be a list"))


def _check_carrier(
    frontmatter: dict[str, object], spec: FormatSpec, report: LintReport
) -> None:
    meta = frontmatter.get("metadata")
    if not isinstance(meta, dict):
        return
    carrier = meta.get(CARRIER)
    if carrier is None:
        return
    if not isinstance(carrier, dict):
        report.issues.append(_error("carrier", f"metadata.{CARRIER} must be a mapping"))
        return

    extra = carrier.get("extra")
    if extra is not None:
        if not isinstance(extra, dict):
            report.issues.append(
                _error("carrier", f"metadata.{CARRIER}.extra must be a mapping")
            )
        else:
            expressible = sorted(
                key
                for key in extra
                if key in spec.fields or key in {"name", "description", "metadata"}
            )
            if expressible:
                report.issues.append(
                    _error(
                        "carrier",
                        f"{report.target_format.value} reads "
                        f"{', '.join(expressible)}; only fields it cannot express "
                        "belong in the carrier",
                    )
                )

    manual = carrier.get("manual-invocation")
    if manual is not None and not isinstance(manual, bool):
        report.issues.append(
            _error("carrier", "manual-invocation must be true or false")
        )
    unknown = sorted(set(carrier) - {"extra", "manual-invocation"})
    if unknown:
        report.issues.append(
            _warn("carrier", f"unknown carrier key(s): {', '.join(unknown)}")
        )


def _check_body(body: str, spec: FormatSpec, report: LintReport) -> None:
    if not body.strip():
        report.issues.append(_error("body", "SKILL.md has no body"))
        return
    if body.lstrip().startswith("---"):
        report.issues.append(
            _error("body", "body starts with ---, which reads as a second block")
        )
    if spec.body_sections:
        lower = body.lower()
        if not any(section in lower for section in spec.body_sections):
            report.issues.append(
                _error(
                    "sections",
                    f"{report.target_format.value} output should contain "
                    f"{' or '.join(spec.body_sections)}",
                )
            )


def _check_layout(skill_dir: Path, report: LintReport) -> None:
    for child in sorted(skill_dir.iterdir()):
        if child.name.startswith(STAGING_PREFIX):
            report.issues.append(
                _error("layout", f"staging folder {child.name} was left behind")
            )
    if (skill_dir / "reference").is_dir() and not (skill_dir / "references").is_dir():
        report.issues.append(_error("layout", "reference/ must be references/"))
    nested = [path for path in skill_dir.rglob(SKILL_FILE) if path.parent != skill_dir]
    if nested:
        relative = nested[0].relative_to(skill_dir).as_posix()
        report.issues.append(_error("layout", f"nested {SKILL_FILE} at {relative}"))


def _check_marker(skill_dir: Path, target: AgentFormat, report: LintReport) -> None:
    recorded = read_marker(skill_dir).get("target-format")
    if recorded and recorded != target.value:
        report.issues.append(
            _warn(
                "marker",
                f"written for {recorded}, checked against {target.value}",
            )
        )


def lint_matrix(source: Path) -> dict[AgentFormat, LintReport]:
    """Convert ``source`` into every format and lint each result."""
    load_exporters()
    reports: dict[AgentFormat, LintReport] = {}
    root = Path(tempfile.mkdtemp(prefix="skill-bridge-lint-"))
    try:
        for fmt in AgentFormat:
            out = root / fmt.value
            try:
                result = convert_skill(source, fmt, out, validate=False)
            except (OSError, ValueError) as exc:
                report = LintReport(skill_dir=out, target_format=fmt)
                report.issues.append(_error("convert", f"could not convert ({exc})"))
                reports[fmt] = report
                continue
            reports[fmt] = lint_skill(result.output_dir, fmt)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return reports
