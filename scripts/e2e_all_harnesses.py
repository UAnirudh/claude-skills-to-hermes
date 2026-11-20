"""End-to-end: a machine with every harness, then install into each one."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLI = [str(REPO / ".venv" / "bin" / "skill-bridge")]

sys.path.insert(0, str(REPO))
from skill_bridge.parser import parse_frontmatter  # noqa: E402
ENV = {**os.environ}

FORMATS = [
    "claude-code", "cursor", "codex", "hermes", "opencode", "gemini", "copilot",
    "windsurf", "devin", "cline", "roo", "continue", "goose", "openhands",
    "amp", "openagents",
]

# Distinctive per-harness frontmatter, so conversion has to translate, not copy.
EXTRAS = {
    "claude-code": "allowed-tools: Read Grep Bash\nmodel: sonnet\nargument-hint: \"[path]\"\n",
    "cursor": "disable-model-invocation: true\npaths:\n  - \"src/**\"\n",
    "devin": "triggers:\n  - user\nallowed-tools:\n  - read\n  - grep\n",
    "hermes": "version: \"1.0.0\"\nmetadata:\n  hermes:\n    tags:\n      - review\n    category: workflow\n",
    "continue": "metadata:\n  continue:\n    type: skill\n",
    "opencode": "disable-model-invocation: true\nmetadata:\n  opencode:\n    autoinvoke: false\n",
    "copilot": "user-invocable: true\n",
}


def run(cmd: list[str], home: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = {**ENV, "HOME": str(home)}
    return subprocess.run(cmd, env=env, cwd=cwd, text=True, capture_output=True)


def seed_home(home: Path, project: Path) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for fmt in FORMATS:
        out = run(CLI + ["paths", fmt], home, project)
        root = Path(out.stdout.strip())
        roots[fmt] = root
        skill = root / f"seed-{fmt}"
        skill.mkdir(parents=True)
        extra = EXTRAS.get(fmt, "")
        (skill / "SKILL.md").write_text(
            "---\n"
            f"name: seed-{fmt}\n"
            f"description: Skill authored for {fmt} and converted into every other harness.\n"
            f"{extra}"
            "---\n\n"
            f"# Seed {fmt}\n\n"
            "## Steps\n\n1. Do the seed thing.\n",
            encoding="utf-8",
        )
        (skill / "references").mkdir()
        (skill / "references" / "NOTES.md").write_text(f"# Notes for {fmt}\n", encoding="utf-8")
    return roots


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="sb-e2e-"))
    pristine = work / "pristine-home"
    pristine.mkdir()
    project = work / "project"
    project.mkdir()

    roots = seed_home(pristine, project)

    detect = run(CLI + ["detect"], pristine, project)
    found = sum(1 for fmt in FORMATS if roots[fmt].is_dir())
    print(f"detect: {found}/{len(FORMATS)} harnesses found on the machine")
    print(detect.stdout.strip().splitlines()[-1])

    failures: list[str] = []
    for target in FORMATS:
        home = work / f"home-{target}"
        shutil.copytree(pristine, home)
        tproject = work / f"project-{target}"
        tproject.mkdir()

        result = run(CLI + ["install", "--harness", target], home, tproject)
        if result.returncode != 0:
            failures.append(f"{target}: install exited {result.returncode}\n{result.stderr}")
            continue

        target_root = Path(run(CLI + ["paths", target], home, tproject).stdout.strip())
        other_roots = {f: r for f, r in roots.items() if r != target_root}

        missing = [
            f"seed-{fmt}" for fmt in other_roots
            if not (target_root / f"seed-{fmt}" / "SKILL.md").is_file()
        ]
        if missing:
            failures.append(f"{target}: missing imports {missing}")

        native_seed = target_root / f"seed-{target}"
        if (native_seed / ".skill-bridge.json").exists():
            failures.append(f"{target}: overwrote its own hand-written skill")

        for fmt in other_roots:
            marker = target_root / f"seed-{fmt}" / ".skill-bridge.json"
            if marker.is_file() and f'"target-format": "{target}"' not in marker.read_text():
                failures.append(f"{target}: wrong marker for seed-{fmt}")
            notes = target_root / f"seed-{fmt}" / "references" / "NOTES.md"
            if not notes.is_file():
                failures.append(f"{target}: references/ not copied for seed-{fmt}")

        # Re-run: refresh, never duplicate.
        again = run(CLI + ["install", "--harness", target, "--force"], home, tproject)
        if again.returncode != 0 or "update=" not in again.stdout:
            failures.append(f"{target}: re-install did not refresh ({again.returncode})")

        # The written profile must keep it current.
        sync = run(CLI + ["sync"], home, tproject)
        if sync.returncode != 0:
            failures.append(f"{target}: sync failed\n{sync.stderr}")

        added = sorted(p.name for p in target_root.iterdir() if p.is_dir())
        print(f"  {target:<11} imported {len(other_roots):>2} skills -> {target_root}  ({len(added)} total)")

    # Field translation across a real round trip through the filesystem.
    home = work / "home-claude-code"
    claude_skill = home / ".claude" / "skills" / "seed-cursor" / "SKILL.md"
    text = claude_skill.read_text() if claude_skill.is_file() else ""
    if "disable-model-invocation" not in text:
        failures.append("claude-code: cursor's disable-model-invocation was not translated natively")
    codex_skill = work / "home-codex" / ".agents" / "skills" / "seed-claude-code" / "SKILL.md"
    codex_fm, _ = parse_frontmatter(codex_skill.read_text())
    parked = ((codex_fm.get("metadata") or {}).get("skill-bridge") or {}).get("extra") or {}
    if "allowed-tools" in codex_fm or parked.get("allowed-tools") != "Read Grep Bash":
        failures.append("codex: claude extras were not parked in metadata.skill-bridge")

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for failure in failures:
            print(" -", failure)
        return 1
    print(f"OK: installed into all {len(FORMATS)} harnesses, every one picked up all the others")
    print(f"workdir: {work}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
