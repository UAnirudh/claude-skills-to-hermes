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
from skill_bridge.converter import convert_skill, sweep_stale_holders
from skill_bridge.locking import DEFAULT_LOCK_WAIT, exclusive_run, lock_path
from skill_bridge.models import AgentFormat
from skill_bridge.parser import find_skills, load_skill
from skill_bridge.provenance import (
    MARKER_FILENAME,
    bless_marker,
    is_bridge_generated,
    read_marker,
    tree_hash,
)
from skill_bridge.prune import recorded_source, source_disappeared
from skill_bridge.sync import run_prune, run_sync

STALE = "stale"
OUT_OF_SYNC = "out-of-sync"
EDITED = "edited"
LOSSY = "lossy"

#: Findings a command can fix without guessing what the user meant.
FIXABLE_KINDS = (STALE, OUT_OF_SYNC)
#: Findings that mean the installed copies are not what the sources say, and
#: so fail `--strict`. EDITED belongs here because it needs a human decision,
#: not because a command should resolve it.
DRIFT_KINDS = (STALE, OUT_OF_SYNC, EDITED)


@dataclass(frozen=True)
class Finding:
    """One problem with one generated copy."""

    kind: str
    skill_name: str
    target_format: AgentFormat
    skill_dir: Path
    detail: str
    #: Files inside the copy that a fresh conversion writes differently.
    files: list[FileDifference] = field(default_factory=list)


@dataclass
class DoctorReport:
    """What a pass over the profile's targets found."""

    checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    def of_kind(self, kind: str) -> list[Finding]:
        return [finding for finding in self.findings if finding.kind == kind]

    @property
    def drift(self) -> list[Finding]:
        """Findings that mean a copy no longer follows its source."""
        return [finding for finding in self.findings if finding.kind in DRIFT_KINDS]

    @property
    def fixable(self) -> list[Finding]:
        """Findings `doctor --fix` may repair without a judgement call."""
        return [finding for finding in self.findings if finding.kind in FIXABLE_KINDS]

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


CHANGED = "changed"
EXTRA = "extra"
MISSING = "missing"


@dataclass(frozen=True)
class FileDifference:
    """One file inside a copy that a fresh conversion would write differently."""

    name: str
    status: str
    #: 1-based line of the first difference, in ``expected``'s terms.
    line: int = 1


def _first_differing_line(expected: bytes, actual: bytes) -> int:
    """Where two versions of a text file first disagree.

    Files are decoded leniently: a binary difference still yields a line rather
    than an error, which is all an editor annotation needs.
    """
    left = expected.decode("utf-8", errors="replace").splitlines()
    right = actual.decode("utf-8", errors="replace").splitlines()
    for index, (was, now) in enumerate(zip(left, right)):
        if was != now:
            return index + 1
    return min(len(left), len(right)) + 1


def _compare_files(
    expected: dict[str, bytes], actual: dict[str, bytes]
) -> list[FileDifference]:
    """Every file that differs, added or missing, in human-reading order."""
    differences: list[FileDifference] = []
    for name in sorted(set(expected) & set(actual)):
        if expected[name] == actual[name]:
            continue
        differences.append(
            FileDifference(
                name, CHANGED, _first_differing_line(expected[name], actual[name])
            )
        )
    for name in sorted(set(actual) - set(expected)):
        differences.append(FileDifference(name, EXTRA))
    for name in sorted(set(expected) - set(actual)):
        differences.append(FileDifference(name, MISSING))
    return differences


def _describe_difference(differences: list[FileDifference]) -> str:
    parts: list[str] = []
    for status in (CHANGED, EXTRA, MISSING):
        names = [item.name for item in differences if item.status == status]
        if names:
            parts.append(f"{status} " + ", ".join(names))
    return "; ".join(parts) or "content differs"


def _difference(
    skill_dir: Path, source: Path, target: AgentFormat
) -> tuple[str, str, list[FileDifference]] | None:
    """Why ``skill_dir`` no longer matches its source, as (kind, detail, files).

    Markers written since fingerprints were added record the source as it was
    when the copy was made and the copy as it was written. A copy that fails
    its own fingerprint was edited in place, so it is reported as EDITED and
    never refreshed: overwriting it would throw away whatever the user wrote.
    A copy whose source fingerprint changed while its own still matches is
    merely behind, and is the only thing `--fix` regenerates.

    Older markers carry no fingerprints, so a difference there cannot be
    attributed to either side; it is reported as EDITED for the same reason,
    and an explicit `sync` is the user's call.
    """
    marker = read_marker(skill_dir)
    source_sha = marker.get("source-sha")
    output_sha = marker.get("output-sha")
    try:
        copy_changed = bool(output_sha) and tree_hash(skill_dir) != output_sha
        source_changed = bool(source_sha) and tree_hash(source) != source_sha
    except OSError:
        return EDITED, "copy cannot be read", []

    if not (copy_changed or source_changed):
        if source_sha and output_sha:
            return None  # both fingerprints agree: nothing to re-convert
        differences, problem = _reconverted_difference(skill_dir, source, target)
        if problem is not None:
            return EDITED, f"{problem}; no fingerprints to say which side changed", []
        if not differences:
            return None
        return (
            EDITED,
            f"{_describe_difference(differences)}; "
            "no fingerprints to say which side changed",
            differences,
        )

    # Drift with fingerprints: the marker says *that* it drifted, and a fresh
    # conversion is the only way to say which files inside the copy to point a
    # reader at. Only copies already known to be wrong pay for it.
    differences, _problem = _reconverted_difference(skill_dir, source, target)
    if copy_changed and source_changed:
        return EDITED, "edited by hand and its source changed too", differences
    if copy_changed:
        return EDITED, "edited by hand since it was written", differences
    if marker.get("local-changes"):
        # An accepted copy is the user's baseline now: a later source change is
        # reported for a decision, never refreshed over the hand-written text.
        return EDITED, "accepted copy, and its source changed since", differences
    return OUT_OF_SYNC, "source changed since this copy was converted", differences


def _reconverted_difference(
    skill_dir: Path, source: Path, target: AgentFormat
) -> tuple[list[FileDifference], str | None]:
    """Files that differ from converting ``source`` now, and why if unknown."""
    scratch = Path(tempfile.mkdtemp(prefix="skill-bridge-doctor-"))
    try:
        fresh = convert_skill(source, target, scratch, validate=False)
        expected = _snapshot(fresh.output_dir)
    except (OSError, ValueError) as exc:
        return [], f"could not re-convert the source ({exc})"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    actual = _snapshot(skill_dir)
    if expected is None or actual is None:
        return [], "copy cannot be read"
    return _compare_files(expected, actual), None


def diagnose(locations: list[SkillLocation]) -> DoctorReport:
    """Check every generated copy under ``locations``.

    The copies themselves are never written to; the one thing this removes is
    a staging holder a killed conversion left behind days ago.
    """
    report = DoctorReport()
    seen_roots: set[str] = set()
    seen_dirs: set[str] = set()

    for loc in locations:
        root = Path(loc.path)
        sweep_stale_holders(root)
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
                kind, detail, files = difference
                report.findings.append(
                    Finding(kind, skill_dir.name, loc.format, skill_dir, detail, files)
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
    edited: int = 0
    lossy: int = 0

    @property
    def touched(self) -> bool:
        return bool(self.pruned or self.refreshed)


def repair(
    report: DoctorReport,
    *,
    profile_name: str | None = None,
    config_path: Path | None = None,
    lock_wait: float = DEFAULT_LOCK_WAIT,
) -> RepairResult:
    """Apply the fixes ``report`` names, leaving lossy findings alone.

    Stale copies go through ``run_prune`` and behind ones through ``run_sync``
    — the same guarded code paths the commands use, so a copy is still only
    deleted when the marker proves it is ours.

    Only copies the marker proves are behind are refreshed, and one skill at a
    time: a profile-wide sync would also rewrite every hand-edited copy in the
    target, which is exactly what this must not do. EDITED and LOSSY findings
    are counted and left for the user.
    """
    result = RepairResult(
        edited=len(report.of_kind(EDITED)), lossy=len(report.of_kind(LOSSY))
    )
    # One lock for the whole repair: the individual prune and syncs re-enter it
    # rather than taking it again, so nobody sees a half-repaired profile.
    with exclusive_run(lock_path(config_path), wait=lock_wait):
        if report.of_kind(STALE):
            outcome = run_prune(profile_name=profile_name, config_path=config_path)
            result.pruned = len(outcome.removed)

        behind = report.of_kind(OUT_OF_SYNC)
        for skill_name in sorted({finding.skill_name for finding in behind}):
            try:
                conversions = run_sync(
                    profile_name=profile_name,
                    config_path=config_path,
                    skill_name=skill_name,
                )
            except FileNotFoundError:
                # The skill vanished between the report and the repair.
                continue
            result.conversions += len(conversions)
    result.refreshed = len(behind)
    return result


@dataclass
class AcceptResult:
    """Copies whose marker was rewritten to bless their current contents."""

    accepted: list[Finding] = field(default_factory=list)


def accept_edits(
    report: DoctorReport,
    *,
    profile_name: str | None = None,
    config_path: Path | None = None,
    skill_name: str | None = None,
    lock_wait: float = DEFAULT_LOCK_WAIT,
) -> AcceptResult:
    """Bless every hand-edited copy ``report`` names, keeping the edit.

    Only EDITED findings are candidates: a stale copy has no source left to
    bless against, and a lossy one is a property of the format pair rather than
    something a decision can settle. Runs under the profile lock because it
    rewrites markers a concurrent sync may be reading.
    """
    result = AcceptResult()
    candidates = [
        finding
        for finding in report.of_kind(EDITED)
        if skill_name is None or finding.skill_name == skill_name
    ]
    if not candidates:
        return result
    with exclusive_run(lock_path(config_path), wait=lock_wait):
        for finding in candidates:
            bless_marker(finding.skill_dir, source=recorded_source(finding.skill_dir))
            result.accepted.append(finding)
    return result


def report_to_dict(
    report: DoctorReport,
    *,
    profile: str | None = None,
    repair: RepairResult | None = None,
) -> dict[str, object]:
    """Machine-readable view of a report, for CI annotations.

    Every finding carries the directory and the ``SKILL.md`` a CI system can
    annotate, along with the kind that says whether a command can fix it.
    ``fixed`` is present only when the caller actually repaired something.
    """
    payload: dict[str, object] = {
        "profile": profile,
        "checked": report.checked,
        "clean": report.clean,
        "drift": len(report.drift),
        "counts": {
            kind: len(report.of_kind(kind))
            for kind in (STALE, OUT_OF_SYNC, EDITED, LOSSY)
        },
        "findings": [
            {
                "kind": finding.kind,
                "skill": finding.skill_name,
                "target": finding.target_format.value,
                "path": str(finding.skill_dir),
                "file": str(Path(finding.skill_dir) / "SKILL.md"),
                "detail": finding.detail,
                "files": [
                    {
                        "name": item.name,
                        "status": item.status,
                        "line": item.line,
                        "file": str(Path(finding.skill_dir) / item.name),
                    }
                    for item in finding.files
                ],
            }
            for finding in report.findings
        ],
    }
    if repair is not None:
        payload["fixed"] = {
            "stale": repair.pruned,
            "out_of_sync": repair.refreshed,
            "conversions": repair.conversions,
            "edited_left": repair.edited,
            "lossy_left": repair.lossy,
        }
    return payload
