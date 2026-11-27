"""One-pass health check over the copies skill-bridge maintains.

Three questions a user actually asks about their installed skills:

* is this copy still backed by a source (otherwise ``sync --prune`` removes it);
* does it still match what converting that source produces *now* (otherwise
  ``sync`` rewrites it);
* did the conversion lose frontmatter the target harness cannot express;
* was the copy written by the skill-bridge that is running now, or has a
  converter fix since then never reached it.

The first two are drift and can be fixed by a command; the third is a property
of the format pair, so it is reported but never treated as drift. A copy whose
marker records an older skill-bridge than the one running is ``outdated``: its
contents can still be exactly what the source says, so it is information rather
than drift, and ``sync`` — not ``--fix`` — is what re-converts it. Everything
here is read-only: ``doctor`` re-converts into a temporary directory only to
compare, and never touches the copies or the sources.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from skill_bridge import cache
from skill_bridge.compat import lossy_fields
from skill_bridge.config import SkillLocation, SyncProfile
from skill_bridge.converter import convert_skill, sweep_stale_holders
from skill_bridge.locking import DEFAULT_LOCK_WAIT, exclusive_run, lock_path
from skill_bridge.models import AgentFormat
from skill_bridge.parser import find_skills, load_skill
from skill_bridge.provenance import (
    MARKER_FILENAME,
    bless_marker,
    is_bridge_generated,
    package_version,
    read_marker,
    tree_files,
    tree_hash,
)
from skill_bridge.prune import recorded_source, source_disappeared
from skill_bridge.sync import run_prune, run_sync

STALE = "stale"
OUT_OF_SYNC = "out-of-sync"
EDITED = "edited"
LOSSY = "lossy"
#: Written by a different, older skill-bridge than the one running.
OUTDATED = "outdated"

#: Version of the `doctor --json` document. Bumped whenever a consumer could
#: notice a change: a field removed or renamed, a type changed, a new required
#: field. Adding an optional field does not need a bump, and neither does a
#: change to the human output.
REPORT_VERSION = 1

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
    #: Profiles that named copies already checked earlier in the same run.
    reused_from: set[str] = field(default_factory=set)

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
    return tree_files(root)


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
        # Taken once: it decides drift and names the cache entry of the
        # conversion below.
        current_source = tree_hash(source)
    except OSError:
        return EDITED, "copy cannot be read", []
    source_changed = bool(source_sha) and current_source != source_sha

    if not (copy_changed or source_changed):
        if source_sha and output_sha:
            return None  # both fingerprints agree: nothing to re-convert
        differences, problem = _reconverted_difference(
            skill_dir, source, target, current_source
        )
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
    differences, _problem = _reconverted_difference(
        skill_dir, source, target, current_source
    )
    if copy_changed and source_changed:
        return EDITED, "edited by hand and its source changed too", differences
    if copy_changed:
        return EDITED, "edited by hand since it was written", differences
    if marker.get("local-changes"):
        # An accepted copy is the user's baseline now: a later source change is
        # reported for a decision, never refreshed over the hand-written text.
        return EDITED, "accepted copy, and its source changed since", differences
    return OUT_OF_SYNC, "source changed since this copy was converted", differences


def _version_key(value: str) -> tuple[int, ...] | None:
    """Comparable parts of a version string, or None when it is not one.

    Only the leading number of each dot-separated piece counts, so ``1.4.0rc1``
    reads as 1.4.0, and trailing zeros are dropped so ``1.4`` and ``1.4.0`` are
    the same version rather than an upgrade.
    """
    parts: list[int] = []
    for piece in str(value).split("."):
        digits = ""
        for char in piece:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            return None
        parts.append(int(digits))
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _outdated(skill_dir: Path) -> str | None:
    """Why this copy was written by an older skill-bridge, if it was.

    The marker records the version that wrote the copy. Both sides are read the
    way ``write_marker`` writes them, so a checkout whose installed metadata
    lags its source still compares a copy against the version that would have
    written it. A version that cannot be read as a number is not evidence of
    anything, and a copy from a *newer* skill-bridge is a downgrade rather than
    an outdated copy, so neither is reported.
    """
    recorded = read_marker(skill_dir).get("version")
    if not recorded:
        return None
    current = package_version()
    old, new = _version_key(str(recorded)), _version_key(current)
    if old is None or new is None or old >= new:
        return None
    return f"converted by skill-bridge {recorded}, running {current}"


def _reconverted_difference(
    skill_dir: Path, source: Path, target: AgentFormat, source_hash: str
) -> tuple[list[FileDifference], str | None]:
    """Files that differ from converting ``source`` now, and why if unknown.

    Converting is the expensive half of a check and it is deterministic, so the
    result is kept in :mod:`skill_bridge.cache`: the same source, target and
    converter always produce the same bytes, and a second check reads them back
    instead of paying for the conversion again.
    """
    cached = cache.load(source_hash, target)
    if cached is not None:
        actual = _snapshot(skill_dir)
        if actual is None:
            return [], "copy cannot be read"
        return _compare_files(cached, actual), None

    scratch = Path(tempfile.mkdtemp(prefix="skill-bridge-doctor-"))
    try:
        fresh = convert_skill(source, target, scratch, validate=False)
        expected = _snapshot(fresh.output_dir)
    except (OSError, ValueError) as exc:
        return [], f"could not re-convert the source ({exc})"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if expected is None:
        return [], "copy cannot be read"
    cache.store(source_hash, target, expected)
    actual = _snapshot(skill_dir)
    if actual is None:
        return [], "copy cannot be read"
    return _compare_files(expected, actual), None


@dataclass
class _Pass:
    """Copies already checked in this run, and which profile checked them.

    Harnesses can share a folder (Codex and Amp both use ``~/.agents/skills``)
    and so can two profiles, so each copy is checked once and remembered. The
    owner is the profile that saw it first, which is what lets a later profile
    say its copies were covered instead of repeating the same finding.
    """

    #: Real path of each folder walked, and the copies found in it.
    roots: dict[str, list[str]] = field(default_factory=dict)
    #: Real path of each copy checked, and the profile that checked it.
    dirs: dict[str, str] = field(default_factory=dict)
    owner: str | None = None

    def check(self, locations: list[SkillLocation], report: DoctorReport) -> None:
        for loc in locations:
            root = Path(loc.path)
            sweep_stale_holders(root)
            if not root.is_dir():
                continue
            # Codex and Amp share ~/.agents/skills, and so can two profiles:
            # walk each real folder once and remember the copies it holds.
            root_key = os.path.realpath(root)
            if root_key in self.roots:
                self._covered(self.roots[root_key], report)
                continue
            self.roots[root_key] = []

            for skill_dir in find_skills(root):
                if not is_bridge_generated(skill_dir):
                    continue  # hand-written: nothing to be in or out of sync with
                real = os.path.realpath(skill_dir)
                self.roots[root_key].append(real)
                if real in self.dirs:
                    self._covered([real], report)
                    continue
                self.dirs[real] = self.owner or ""
                report.checked += 1
                self._check_copy(skill_dir, loc.format, report)

    def _covered(self, copies: list[str], report: DoctorReport) -> None:
        """Note copies another profile already checked, instead of repeating."""
        for real in copies:
            first = self.dirs.get(real)
            if first and first != self.owner:
                report.reused_from.add(first)

    @staticmethod
    def _check_copy(
        skill_dir: Path, target: AgentFormat, report: DoctorReport
    ) -> None:
        """Add this copy's findings, in the order they are worth reading."""
        reason = source_disappeared(skill_dir)
        if reason is not None:
            report.findings.append(
                Finding(STALE, skill_dir.name, target, skill_dir, reason)
            )
            return  # no source left to compare or measure loss against

        source = recorded_source(skill_dir)
        if source is None:
            return  # no absolute source path: cannot prove anything

        try:
            bundle = load_skill(source)
        except (OSError, ValueError):
            bundle = None
        lost = lossy_fields(bundle.frontmatter, target) if bundle is not None else []

        difference = _difference(skill_dir, source, target)
        if difference is not None:
            kind, detail, files = difference
            report.findings.append(
                Finding(kind, skill_dir.name, target, skill_dir, detail, files)
            )
        else:
            # Only a copy with nothing else wrong is worth a version note: when
            # it already needs a sync, that finding is the one to act on, and a
            # second line about the same copy is noise.
            stale_version = _outdated(skill_dir)
            if stale_version is not None:
                report.findings.append(
                    Finding(OUTDATED, skill_dir.name, target, skill_dir, stale_version)
                )
        if lost:
            report.findings.append(
                Finding(LOSSY, skill_dir.name, target, skill_dir, ", ".join(lost))
            )


def diagnose(locations: list[SkillLocation]) -> DoctorReport:
    """Check every generated copy under ``locations``.

    The copies themselves are never written to; the one thing this removes is
    a staging holder a killed conversion left behind days ago.

    A copy that is behind costs one conversion to say which files differ, and
    that conversion is deterministic, so its result is cached: checking the
    same profile again reads it back instead of paying for it twice.
    """
    report = DoctorReport()
    _Pass().check(locations, report)
    return report


def diagnose_profiles(profiles: list[SyncProfile]) -> dict[str, DoctorReport]:
    """Check several profiles in one pass, reporting each copy once.

    Profiles in one config often cover the same folder — `install` writes an
    `auto-import` profile beside the user's own, and both can name the same
    harness — so the check is shared: a copy is reported under the first
    profile that names it, and a later profile that would have covered it
    records that in ``reused_from`` rather than repeating the finding.
    """
    run = _Pass()
    reports: dict[str, DoctorReport] = {}
    for profile in profiles:
        report = DoctorReport()
        run.owner = profile.name
        run.check(profile.targets, report)
        reports[profile.name] = report
    return reports


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


def report_schema() -> dict[str, object]:
    """The JSON Schema of the document `doctor --json` writes.

    Written out rather than derived, because its readers are other people's CI
    scripts: a schema is documentation first and a validator second. The
    contract is `report-version`; the tests keep the two in step.
    """
    count = {"type": "integer", "minimum": 0}

    def counts_schema() -> dict[str, object]:
        """Per-kind finding counts, shared by the report and each profile."""
        return {
            "type": "object",
            "additionalProperties": False,
            # `outdated` came after version 1 was published, so it is optional
            # here: a document written before it still validates, and a
            # consumer that has never heard of it can ignore the key.
            "required": [STALE, OUT_OF_SYNC, EDITED, LOSSY],
            "properties": {
                kind: count
                for kind in (STALE, OUT_OF_SYNC, EDITED, LOSSY, OUTDATED)
            },
        }

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:skill-bridge:doctor-report:{REPORT_VERSION}",
        "title": f"skill-bridge doctor report, version {REPORT_VERSION}",
        "type": "object",
        "required": [
            "report-version",
            "profile",
            "checked",
            "clean",
            "drift",
            "counts",
            "findings",
        ],
        "properties": {
            "report-version": {"const": REPORT_VERSION},
            "profile": {"type": ["string", "null"]},
            "checked": count,
            "clean": {"type": "boolean"},
            "drift": count,
            "counts": counts_schema(),
            # Present only for `doctor --all-profiles`: one entry per profile,
            # so a CI script can report per profile without splitting findings.
            "profiles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["profile", "checked", "clean", "drift", "counts"],
                    "properties": {
                        "profile": {"type": "string"},
                        "checked": count,
                        "clean": {"type": "boolean"},
                        "drift": count,
                        "counts": counts_schema(),
                    },
                },
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "kind",
                        "skill",
                        "target",
                        "path",
                        "file",
                        "detail",
                        "files",
                    ],
                    "properties": {
                        "kind": {
                            "enum": [STALE, OUT_OF_SYNC, EDITED, LOSSY, OUTDATED]
                        },
                        "skill": {"type": "string"},
                        "target": {"type": "string"},
                        "path": {"type": "string"},
                        "file": {"type": "string"},
                        "detail": {"type": "string"},
                        # Only when one run covered several profiles.
                        "profile": {"type": "string"},
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["name", "status", "line", "file"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "status": {
                                        "enum": [CHANGED, EXTRA, MISSING]
                                    },
                                    "line": {"type": "integer", "minimum": 1},
                                    "file": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
            "fixed": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "stale",
                    "out_of_sync",
                    "conversions",
                    "edited_left",
                    "lossy_left",
                ],
                "properties": {
                    "stale": count,
                    "out_of_sync": count,
                    "conversions": count,
                    "edited_left": count,
                    "lossy_left": count,
                },
            },
        },
    }


def _finding_dict(
    finding: Finding, profile: str | None = None
) -> dict[str, object]:
    """One finding as a document, with the file a CI system can annotate."""
    payload: dict[str, object] = {
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
    if profile is not None:
        payload["profile"] = profile
    return payload


def _counts(report: DoctorReport) -> dict[str, int]:
    return {
        kind: len(report.of_kind(kind))
        for kind in (STALE, OUT_OF_SYNC, EDITED, LOSSY, OUTDATED)
    }


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
        "report-version": REPORT_VERSION,
        "profile": profile,
        "checked": report.checked,
        "clean": report.clean,
        "drift": len(report.drift),
        "counts": _counts(report),
        "findings": [_finding_dict(finding) for finding in report.findings],
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


def profiles_to_dict(
    reports: dict[str, DoctorReport],
    *,
    repair: dict[str, RepairResult] | None = None,
) -> dict[str, object]:
    """Machine-readable view of one run over several profiles.

    The same shape `report_to_dict` writes, with `profile` null, one entry per
    profile in `profiles`, and every finding tagged with the profile it belongs
    to. Totals cover the whole run, and a copy two profiles share is counted
    once, under the profile that checked it.
    """
    totals = {kind: 0 for kind in (STALE, OUT_OF_SYNC, EDITED, LOSSY, OUTDATED)}
    findings: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    for name, report in reports.items():
        counts = _counts(report)
        for kind, value in counts.items():
            totals[kind] += value
        findings.extend(_finding_dict(finding, name) for finding in report.findings)
        summaries.append(
            {
                "profile": name,
                "checked": report.checked,
                "clean": report.clean,
                "drift": len(report.drift),
                "counts": counts,
            }
        )

    payload: dict[str, object] = {
        "report-version": REPORT_VERSION,
        "profile": None,
        "profiles": summaries,
        "checked": sum(report.checked for report in reports.values()),
        "clean": all(report.clean for report in reports.values()),
        "drift": sum(len(report.drift) for report in reports.values()),
        "counts": totals,
        "findings": findings,
    }
    if repair is not None:
        payload["fixed"] = {
            "stale": sum(result.pruned for result in repair.values()),
            "out_of_sync": sum(result.refreshed for result in repair.values()),
            "conversions": sum(result.conversions for result in repair.values()),
            "edited_left": sum(result.edited for result in repair.values()),
            "lossy_left": sum(result.lossy for result in repair.values()),
        }
    return payload
