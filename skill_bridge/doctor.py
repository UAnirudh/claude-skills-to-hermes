"""One-pass health check over the copies skill-bridge maintains.

Three questions a user actually asks about their installed skills:

* is this copy still backed by a source (otherwise ``sync --prune`` removes it);
* does it still match what converting that source produces *now* (otherwise
  ``sync`` rewrites it);
* did the conversion lose frontmatter the target harness cannot express.

The first two are drift and can be fixed by a command; the third is a property
of the format pair, so it is reported but never treated as drift. Everything
here is read-only: ``doctor`` re-converts into a temporary directory only to
compare, and never touches the copies or the sources.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from skill_bridge.compat import lossy_fields
from skill_bridge.config import SkillLocation
from skill_bridge.converter import convert_skill
from skill_bridge.models import AgentFormat
from skill_bridge.parser import find_skills, load_skill
from skill_bridge.provenance import MARKER_FILENAME, is_bridge_generated
from skill_bridge.prune import recorded_source, source_disappeared
from skill_bridge.sync import run_prune, run_sync

STALE = "stale"
OUT_OF_SYNC = "out-of-sync"
LOSSY = "lossy"

#: Findings a command can fix, which is what `--strict` gates on.
DRIFT_KINDS = (STALE, OUT_OF_SYNC)


@dataclass(frozen=True)
class Finding:
    """One problem with one generated copy."""

    kind: str
    skill_name: str
    target_format: AgentFormat
    skill_dir: Path
    detail: str


@dataclass
class DoctorReport:
    """What a pass over the profile's targets found."""

    checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    def of_kind(self, kind: str) -> list[Finding]:
        return [finding for finding in self.findings if finding.kind == kind]

    @property
    def drift(self) -> list[Finding]:
        """Findings that `sync` or `sync --prune` can fix."""
        return [finding for finding in self.findings if finding.kind in DRIFT_KINDS]

    @property
    def clean(self) -> bool:
        return not self.findings


def _snapshot(root: Path) -> dict[str, bytes] | None:
    """Every file under ``root`` except the provenance marker, or None."""
    files: dict[str, bytes] = {}
    try:
        for entry in sorted(root.rglob("*")):
            if entry.is_dir() or entry.name == MARKER_FILENAME:
                continue
            files[entry.relative_to(root).as_posix()] = entry.read_bytes()
    except OSError:
        return None
    return files


def _describe_difference(expected: dict[str, bytes], actual: dict[str, bytes]) -> str:
    changed = sorted(
        name for name in set(expected) & set(actual) if expected[name] != actual[name]
    )
    extra = sorted(set(actual) - set(expected))
    missing = sorted(set(expected) - set(actual))
    parts: list[str] = []
    if changed:
        parts.append("changed " + ", ".join(changed))
    if extra:
        parts.append("extra " + ", ".join(extra))
    if missing:
        parts.append("missing " + ", ".join(missing))
    return "; ".join(parts) or "content differs"


def _difference(skill_dir: Path, source: Path, target: AgentFormat) -> str | None:
    """Why ``skill_dir`` differs from converting ``source`` now, or None."""
    scratch = Path(tempfile.mkdtemp(prefix="skill-bridge-doctor-"))
    try:
        fresh = convert_skill(source, target, scratch, validate=False)
        expected = _snapshot(fresh.output_dir)
    except (OSError, ValueError) as exc:
        return f"could not re-convert the source ({exc})"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    actual = _snapshot(skill_dir)
    if expected is None or actual is None:
        return "copy cannot be read"
    if expected == actual:
        return None
    return _describe_difference(expected, actual)


def diagnose(locations: list[SkillLocation]) -> DoctorReport:
    """Check every generated copy under ``locations``, without writing anything."""
    report = DoctorReport()
    seen_roots: set[str] = set()
    seen_dirs: set[str] = set()

    for loc in locations:
        root = Path(loc.path)
        if not root.is_dir():
            continue
        # Codex and Amp share ~/.agents/skills: check each real folder once.
        root_key = os.path.realpath(root)
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)

        for skill_dir in find_skills(root):
            if not is_bridge_generated(skill_dir):
                continue  # hand-written: nothing to be in or out of sync with
            real = os.path.realpath(skill_dir)
            if real in seen_dirs:
                continue
            seen_dirs.add(real)
            report.checked += 1

            reason = source_disappeared(skill_dir)
            if reason is not None:
                report.findings.append(
                    Finding(STALE, skill_dir.name, loc.format, skill_dir, reason)
                )
                continue  # no source left to compare or measure loss against

            source = recorded_source(skill_dir)
            if source is None:
                continue  # no absolute source path: cannot prove anything

            difference = _difference(skill_dir, source, loc.format)
            if difference is not None:
                report.findings.append(
                    Finding(
                        OUT_OF_SYNC,
                        skill_dir.name,
                        loc.format,
                        skill_dir,
                        difference,
                    )
                )

            try:
                bundle = load_skill(source)
            except (OSError, ValueError):
                continue
            lost = lossy_fields(bundle.frontmatter, loc.format)
            if lost:
                report.findings.append(
                    Finding(LOSSY, skill_dir.name, loc.format, skill_dir, ", ".join(lost))
                )
    return report


@dataclass
class RepairResult:
    """What `doctor --fix` changed, and what it deliberately did not."""

    pruned: int = 0
    refreshed: int = 0
    conversions: int = 0
    lossy: int = 0

    @property
    def touched(self) -> bool:
        return bool(self.pruned or self.refreshed)


def repair(
    report: DoctorReport,
    *,
    profile_name: str | None = None,
    config_path: Path | None = None,
) -> RepairResult:
    """Apply the fixes ``report`` names, leaving lossy findings alone.

    Stale copies go through ``run_prune`` and out-of-sync ones through
    ``run_sync`` — the same guarded code paths the commands use, so a copy is
    still only deleted when the marker proves it is ours. Each is skipped when
    the report holds no finding of that kind, so a clean profile is never
    rewritten. A lossy conversion cannot be repaired by writing files: the
    target format has no field to hold the value, so those are counted and
    left for the user.
    """
    result = RepairResult(lossy=len(report.of_kind(LOSSY)))
    if report.of_kind(STALE):
        outcome = run_prune(profile_name=profile_name, config_path=config_path)
        result.pruned = len(outcome.removed)

    out_of_sync = report.of_kind(OUT_OF_SYNC)
    if out_of_sync:
        try:
            conversions = run_sync(profile_name=profile_name, config_path=config_path)
        except FileNotFoundError:
            # Nothing left to import is not a repair failure.
            conversions = []
        result.conversions = len(conversions)
        result.refreshed = len(out_of_sync)
    return result
