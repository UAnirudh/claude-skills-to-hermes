"""What ``sync`` would change, shown before it changes anything.

`doctor` says *that* a copy is behind; this says *what* the difference is, line
by line, by converting the source into a temporary directory and diffing the
result against the copy that is installed. Nothing is written to the copy or the
source, so it is safe to run on a repository that is not committed yet, and the
diff of a hand-edited copy is exactly the text a sync would overwrite.
"""

from __future__ import annotations

import difflib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from skill_bridge.config import SkillLocation
from skill_bridge.converter import convert_skill
from skill_bridge.doctor import CHANGED, EXTRA, MISSING
from skill_bridge.models import AgentFormat
from skill_bridge.parser import find_skills
from skill_bridge.provenance import is_bridge_generated, tree_files
from skill_bridge.prune import recorded_source, source_disappeared


@dataclass(frozen=True)
class FileChange:
    """One file inside a copy that converting the source would write differently."""

    name: str
    status: str
    #: The copy's bytes, or None when conversion would create the file.
    before: bytes | None
    #: The bytes conversion would write, or None when it would drop the file.
    after: bytes | None


@dataclass
class SkillDiff:
    """Every change one copy would see, in the order a reader wants them."""

    skill_name: str
    target_format: AgentFormat
    skill_dir: Path
    changes: list[FileChange] = field(default_factory=list)


@dataclass
class DiffReport:
    """What a pass over a profile's targets would change."""

    checked: int = 0
    diffs: list[SkillDiff] = field(default_factory=list)
    #: Copies that could not be compared, with the reason.
    problems: list[str] = field(default_factory=list)

    @property
    def files(self) -> int:
        return sum(len(diff.changes) for diff in self.diffs)


def _changes(expected: dict[str, bytes], actual: dict[str, bytes]) -> list[FileChange]:
    """Files that differ, added or missing, in the order doctor lists them."""
    changes: list[FileChange] = []
    for name in sorted(set(expected) & set(actual)):
        if expected[name] == actual[name]:
            continue
        changes.append(FileChange(name, CHANGED, actual[name], expected[name]))
    for name in sorted(set(actual) - set(expected)):
        changes.append(FileChange(name, EXTRA, actual[name], None))
    for name in sorted(set(expected) - set(actual)):
        changes.append(FileChange(name, MISSING, None, expected[name]))
    return changes


def _diff_one(
    skill_dir: Path, source: Path, target: AgentFormat
) -> tuple[SkillDiff | None, str | None]:
    """One copy's changes, or the reason they could not be worked out."""
    scratch = Path(tempfile.mkdtemp(prefix="skill-bridge-diff-"))
    try:
        fresh = convert_skill(source, target, scratch, validate=False)
        expected = tree_files(fresh.output_dir)
    except (OSError, ValueError) as exc:
        return None, f"could not re-convert the source ({exc})"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    actual = tree_files(skill_dir)
    if expected is None or actual is None:
        return None, "copy cannot be read"
    changes = _changes(expected, actual)
    if not changes:
        return None, None
    return SkillDiff(skill_dir.name, target, skill_dir, changes), None


def diff_profile(
    locations: list[SkillLocation], *, skill_name: str | None = None
) -> DiffReport:
    """Diff every generated copy under ``locations`` against its source."""
    report = DiffReport()
    seen_roots: set[str] = set()
    seen_dirs: set[str] = set()

    for loc in locations:
        root = Path(loc.path)
        if not root.is_dir():
            continue
        # Codex and Amp share ~/.agents/skills: diff each real folder once.
        root_key = os.path.realpath(root)
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)

        for skill_dir in find_skills(root):
            if not is_bridge_generated(skill_dir):
                continue  # hand-written: nothing it was converted from
            if skill_name is not None and skill_dir.name != skill_name:
                continue
            real = os.path.realpath(skill_dir)
            if real in seen_dirs:
                continue
            seen_dirs.add(real)
            report.checked += 1

            if source_disappeared(skill_dir) is not None:
                continue  # nothing left to convert: `sync --prune` is the answer
            source = recorded_source(skill_dir)
            if source is None:
                continue  # no absolute source path: cannot convert anything

            diff, problem = _diff_one(skill_dir, source, loc.format)
            if problem is not None:
                report.problems.append(
                    f"{skill_dir.name} ({loc.format.value}): {problem}"
                )
            elif diff is not None:
                report.diffs.append(diff)
    return report


def _relative(path: Path) -> str:
    """A path a reader can copy, relative to the working directory when inside it."""
    try:
        return Path(path).resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _lines(blob: bytes | None) -> list[str]:
    """A file's lines with their endings, decoded leniently."""
    if blob is None:
        return []
    return blob.decode("utf-8", errors="replace").splitlines(keepends=True)


def _unified(change: FileChange) -> list[str]:
    """Git-style unified diff of one file, headers and newline markers included.

    Labels are relative to the copy, which the block above the diff names, so
    the output stays the same wherever the copy lives and `patch -p1` run in the
    copy's directory applies it.
    """
    fromfile = f"a/{change.name}" if change.before is not None else "/dev/null"
    tofile = f"b/{change.name}" if change.after is not None else "/dev/null"
    lines: list[str] = []
    for line in difflib.unified_diff(
        _lines(change.before), _lines(change.after), fromfile, tofile
    ):
        if line.endswith("\n"):
            lines.append(line[:-1])
        else:
            # The file's last line has no newline of its own; saying so keeps the
            # output a patch that applies, as git does.
            lines.append(line)
            lines.append("\\ No newline at end of file")
    return lines


def render(report: DiffReport) -> list[str]:
    """The report as diff lines: one block per copy, git headers and all."""
    lines: list[str] = []
    for diff in report.diffs:
        if lines:
            lines.append("")
        lines.append(
            f"{diff.skill_name} ({diff.target_format.value})  "
            f"{_relative(diff.skill_dir)}"
        )
        for change in diff.changes:
            lines.extend(_unified(change))
    return lines
