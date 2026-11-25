# Skill Bridge

[![CI](https://github.com/UAnirudh/claude-skills-to-hermes/actions/workflows/ci.yml/badge.svg)](https://github.com/UAnirudh/claude-skills-to-hermes/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/spec-agentskills.io-green.svg)](https://agentskills.io)

**Convert and sync Agent Skills between every coding harness** — Claude Code, Hermes Agent, OpenAI Codex, Cursor, Windsurf, Cline, Roo Code, Continue.dev, OpenCode, Gemini CLI, GitHub Copilot, Goose, OpenHands, Amp/Zed/Kilo/Warp, Devin Desktop, and the [agentskills.io](https://agentskills.io) open standard. Author once, run everywhere.

## Why Skill Bridge?

Every major coding agent now uses the same core format: a folder with `SKILL.md` (YAML frontmatter + Markdown) plus optional `scripts/`, `references/`, and `assets/`. The differences are install paths and a few metadata conventions. Skill Bridge knows all of them and converts in **any direction** — all 16 harness formats to all 16, tested pairwise.

| Feature | Command |
|---------|---------|
| Install into your harness and pull in everything else | `skill-bridge install --harness hermes` |
| Import from every other harness | `skill-bridge adopt --to cursor` |
| One-shot convert | `skill-bridge convert ./my-skill --to hermes --to codex` |
| Reverse sync (Hermes → Claude) | `skill-bridge reverse --from hermes --to claude-code` |
| Profile-based sync | `skill-bridge sync` |
| Delete copies whose source was deleted or renamed | `skill-bridge sync --prune` |
| Remove everything Skill Bridge added to a harness | `skill-bridge uninstall --harness hermes` |
| Check for stale, out-of-sync and lossy skills | `skill-bridge doctor` |
| Live watch mode | `skill-bridge watch` |
| Batch all skills | `skill-bridge batch ~/.claude/skills --all-agents` |

## Quick start (30 seconds)

### Windows

```powershell
git clone https://github.com/UAnirudh/claude-skills-to-hermes.git
cd claude-skills-to-hermes
.\setup.ps1
.\.venv\Scripts\Activate.ps1
skill-bridge detect
```

### macOS / Linux

```bash
git clone https://github.com/UAnirudh/claude-skills-to-hermes.git
cd claude-skills-to-hermes
chmod +x setup.sh && ./setup.sh
source .venv/bin/activate
skill-bridge detect
```

### pip only (no clone)

```bash
pip install skill-bridge
skill-bridge install --harness hermes   # or claude-code, cursor, …
```

## Install it on your harness — it imports everything else

This is the whole point: you install Skill Bridge into the harness you use, and it makes skills authored for other harnesses work in yours.

```bash
skill-bridge detect                     # which harnesses live on this machine
skill-bridge install --harness hermes   # convert every other harness's skills into Hermes
```

`install`:

1. detects the skill directories of every other harness on the machine,
2. converts each skill into your harness's layout (frontmatter normalized, `references/`/`scripts/`/`assets/` copied),
3. writes a `skill-bridge.yaml` profile called `auto-import` so `skill-bridge sync` and `skill-bridge watch` keep them current (`watch` follows every source the profile resolves, including harnesses installed while it runs; a `--scope project` install writes a project-scoped profile that keeps refreshing the project copy),
4. records a `.skill-bridge.json` provenance marker in each converted skill.

With no `--harness`, `install` uses the only harness detected on the machine and, when several are installed, shows a numbered picker (`all` in the picker installs into every one). Detection follows `--scope`, so `install --scope project` offers the harnesses that live in this project. `--all` does the same without prompting and writes one `auto-import` profile whose `targets:` list covers each harness, so `sync` fans a change in any of them out to all the rest. Re-running `install --all` refreshes that profile instead of leaving it stale: newly detected harnesses are added, while targets already in it, any `sources:` you edited, and other profiles stay as they are. Harnesses that share one skill directory (Codex and Amp both use `~/.agents/skills`) are installed into once.

Re-running `install` also keeps the profile pointed at the copy you just wrote: a different `--scope` or `--harness` moves the `auto-import` targets instead of leaving `sync` to refresh a stale global copy, and `--scope both` installs the global and project copies in one run and keeps both fresh. A config file that has no readable `auto-import` profile is never rewritten silently: `install` names it and suggests `--force`.

Useful flags:

```bash
skill-bridge install --harness cursor --dry-run        # preview, write nothing
skill-bridge install --all                             # every harness detected here
skill-bridge install --harness claude-code --from codex --from gemini
skill-bridge install --harness opencode --scope project
skill-bridge adopt --to copilot                        # import without writing config
skill-bridge adopt --to cline --force                  # overwrite hand-written skills too
```

Safety rules that make automatic import safe to re-run:

* a skill your harness authored itself is **never** overwritten without `--force`;
* skills converted earlier are refreshed (not duplicated);
* each conversion is staged in a temporary directory beside the skills folder and renamed into place, so an interrupted or concurrent sync can never leave a half-written skill behind, and staging folders a killed run left behind are swept by `sync` and `doctor` once they are a day old;
* a copy whose source was deleted or renamed can be removed with `sync --prune` (or `uninstall`), and a skill your harness wrote itself is never deleted;
* when several harnesses store skills in the same folder (`.agents/skills` is shared by Codex, Cursor, Gemini CLI, Copilot, OpenCode, Cline, Amp, Zed, Kilo Code, Warp), the destination folder is never treated as a source;
* duplicate skill names across harnesses are imported once.

## Supported harnesses

| Format ID | Harness | Project path | Global path |
|-----------|---------|--------------|-------------|
| `claude-code` | Claude Code | `.claude/skills` | `~/.claude/skills` |
| `cursor` | Cursor | `.cursor/skills` | `~/.cursor/skills` |
| `codex` | OpenAI Codex | `.agents/skills` | `~/.agents/skills` |
| `hermes` | Hermes Agent | `.hermes/skills` | `~/.hermes/skills` |
| `opencode` | OpenCode | `.opencode/skills` | `~/.config/opencode/skills` |
| `gemini` | Gemini CLI | `.gemini/skills` | `~/.gemini/skills` |
| `copilot` | GitHub Copilot (agent, review, CLI, VS Code) | `.github/skills` | `~/.copilot/skills` |
| `windsurf` | Windsurf Cascade | `.windsurf/skills` | `~/.codeium/windsurf/skills` |
| `devin` | Devin Desktop (Devin Local) | `.devin/skills` | `~/.config/devin/skills` |
| `cline` | Cline | `.cline/skills` | `~/.cline/skills` |
| `roo` | Roo Code | `.roo/skills` | `~/.roo/skills` |
| `continue` | Continue.dev | `.continue/skills` | `~/.continue/skills` |
| `goose` | Goose | `.goose/skills` | `~/.config/goose/skills` |
| `openhands` | OpenHands | `.openhands/skills` | `~/.openhands/skills` |
| `amp` | Amp, Zed, Kilo Code, Warp | `.agents/skills` | `~/.agents/skills` |
| `openagents` | agentskills.io standard | `skills` | `~/skills` |
| `generic` | Normalized output, no harness conventions | `skills` | `~/skills` |

Skill Bridge writes each harness's documented first-choice location, re-checked against the current docs: [Cline](https://docs.cline.bot/customization/skills), [Devin Desktop](https://docs.devin.ai/desktop/devin-local/customizations), [Cursor](https://cursor.com/docs/skills), [Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills), [Gemini CLI](https://geminicli.com/docs/cli/skills), [Codex](https://developers.openai.com/codex/build-skills), [Roo Code](https://docs.roocode.com/features/skills), [OpenCode](https://opencode.ai/docs/skills), [Amp](https://ampcode.com/docs/customize/skills), [Claude Code](https://code.claude.com/docs/en/skills), [OpenHands](https://docs.openhands.dev/overview/skills) and [Goose](https://goose-docs.ai/docs/guides/context-engineering/using-skills). Windsurf is now Devin Desktop, so both pairs work: `windsurf` keeps the legacy `.windsurf/skills` paths Cascade still reads, and `devin` writes the newer `.devin/skills` ones, while the shared `.agents/skills` folder stays the portable `codex`/`amp` target.

Run `skill-bridge list-formats` for the same table with descriptions, and `skill-bridge detect` to see which of them exist on your machine (`detect --scope project` or `--scope both` also covers project-local directories).

Aliases are accepted anywhere a format is: `gemini-cli`, `github-copilot`, `open-code`, `continue-dev`, `goose-cli`, `claude`, `openai`, `agentskills`, `zed`, `kilo`, `warp`, and more.

## Configuration

Run `skill-bridge init` to generate `skill-bridge.yaml`, or copy the example:

```bash
cp skill-bridge.yaml.example skill-bridge.yaml
```

Example profiles:

```yaml
default_profile: auto-import

profiles:
  # Pull skills from every other installed harness into yours (written by `install`)
  auto-import:
    sources: auto          # or a list: [claude-code, cursor, codex]
    targets:
      - format: hermes
        path: ~/.hermes/skills
    watch: true
    validate: true

  # Classic fan-out: author in one harness, publish to several
  default:
    source:
      format: claude-code
      path: ~/.claude/skills
    targets:
      - format: codex
        path: ~/.agents/skills
      - format: gemini
        path: ~/.gemini/skills
    watch: true
    validate: true
```

Then:

```bash
skill-bridge sync                    # sync all skills once
skill-bridge sync --prune            # also delete copies whose source was deleted/renamed
skill-bridge sync --prune --dry-run  # list those copies, change nothing
skill-bridge watch                   # re-sync on file changes
skill-bridge sync --skill my-skill   # sync one skill
```

`sync` refreshes copies but never deletes them, so rename or delete a source
skill and the old copy stays in every harness. `sync --prune` removes those
copies using the `.skill-bridge.json` marker to prove where each one came
from; `--dry-run` lists them without writing.
`skill-bridge uninstall --harness <id>` does the same for every copy Skill
Bridge created in that harness (`--stale-only` limits it to copies whose
source is gone, `--dry-run` previews either). A skill the harness authored
itself has no marker, so it is never a candidate for either command.

An uninstall also drops the target it emptied from `skill-bridge.yaml`, and
deletes the file once no profile is left, so the next `sync` cannot recreate
what you just removed. Pass `--config PATH` when the profile lives somewhere
`sync` would not find on its own, and that file is updated instead.
`--stale-only` leaves the profile alone: that harness is still meant to
receive skills.

`skill-bridge doctor` checks the whole profile in one read-only pass and groups
what it finds: copies whose source is gone (`sync --prune` fixes those), copies
that no longer match their source or were edited after being written (both
caught by the marker's fingerprints, with a fresh conversion as the fallback
for copies from older versions; `sync` refreshes them), and frontmatter the
target harness cannot express (parked in
`metadata.skill-bridge.extra`, so nothing can restore it in place).
`doctor --strict` exits 1 for the first two — the drift a command can fix — so
CI can gate on it; lossy fields are reported but never fail the gate.
`doctor --fix` applies those two fixes for you (the same guarded paths as
`sync --prune` and `sync`), re-checks the profile, and reports what is left.
A copy edited after it was written is never refreshed — the marker's
fingerprints show the change is in the copy, not the source — so it is listed
as `Edited by hand` and left for you; an explicit `sync` is the way to
regenerate one. Lossy findings stay too, since no conversion can restore a
field the target has no place for. `--fix --strict` exits 0 once nothing needs
a decision.
`doctor --json` writes the same report as a document instead of coloured
text — `profile`, `checked`, `clean`, `drift`, per-kind `counts`, and a
`findings` list where each entry carries the `SKILL.md` to annotate — so CI can
read it without parsing the human output. `--strict` keeps its exit code
either way.

## Reverse conversion

Pull skills from any harness into another:

```bash
# Hermes community skills -> Claude Code
skill-bridge reverse --from hermes --to claude-code

# Codex skills -> Cursor (project scope)
skill-bridge reverse --from codex --to cursor --scope project

# Gemini CLI skills -> Windsurf, custom paths
skill-bridge reverse --from gemini --to windsurf \
  --from-path ~/Downloads/gemini-skills \
  --to-path ~/.codeium/windsurf/skills
```

`reverse` moves one harness into one harness; `adopt` does every harness at once.

## CLI reference

```
skill-bridge init [--source claude-code] [--target hermes] [--target codex]
skill-bridge install [--harness <fmt> | --all] [--from <fmt>] [--scope global|project|both] [--dry-run] [--force]
skill-bridge adopt --to <fmt> [--from <fmt>] [--scope ...] [--dry-run] [--force]
skill-bridge detect [--scope global|project|both]   # every harness + whether it is installed here
skill-bridge list-formats
skill-bridge inspect <skill-dir>
skill-bridge validate <skill-dir> [--harness <fmt> [--fail-on-lossy]]
skill-bridge convert <path> --to <format> [--to ...] [-o output]
skill-bridge reverse --from <fmt> --to <fmt> [--scope global|project]
skill-bridge sync [--profile NAME] [--skill NAME] [--prune [--dry-run]]
skill-bridge doctor [--profile NAME] [-c PATH] [--strict] [--fix] [--json]
skill-bridge uninstall (--harness <fmt> | --all) [--scope ...] [--config PATH] [--stale-only] [--dry-run]
skill-bridge watch [--profile NAME] [--debounce 1.0]
skill-bridge batch <root> --all-agents | --to <format>
skill-bridge paths <format> [--scope global|project|both]
```

## What gets converted

- **SKILL.md** — normalized frontmatter (`name`, `description`, spec-compliant slugs) in a deterministic key order, so repeated conversions are byte-identical
- **Frontmatter fields** — translated per harness, in both directions (see below), not blindly copied
- **Body** — preserved verbatim; Hermes adds `## When to Use` if missing (never twice)
- **Supporting files** — `scripts/`, `references/`, `assets/`, `templates/` copied as-is; `reference/` renamed to `references/`
- **Provenance** — a `.skill-bridge.json` marker in every generated skill, used to refresh safely, never clobber hand-written skills, and delete only bridge-made copies (`sync --prune`, `uninstall`); it records content fingerprints of the source and of the copy as written, so `doctor` detects drift without re-converting anything

## Field translation across harnesses

Every harness reads the same six spec fields (`name`, `description`, `license`,
`compatibility`, `metadata`, `allowed-tools`) and then its own extras. Skill
Bridge keeps a table of what each harness documents reading and translates the
rest instead of dropping it or leaking it:

| Source field | Who reads it natively | Where the intent goes elsewhere |
|--------------|----------------------|---------------------------------|
| `allowed-tools` | Claude Code (space string), Devin (YAML list) | parked in `metadata.skill-bridge.extra` |
| `disable-model-invocation` | Claude Code, Cursor, Copilot, OpenCode | Hermes gets `metadata.hermes.category: workflow`; others get `metadata.skill-bridge.manual-invocation` |
| `user-invocable`, `argument-hint` | Claude Code, Copilot (argument-hint also Devin) | parked in `metadata.skill-bridge.extra`; a model-only skill (`triggers: [model]`, `user-invocable: false`) becomes `user-invocable: false` |
| `model`, `context`, `agent`, `hooks`, `paths` | Claude Code (`paths` also Cursor) | parked in `metadata.skill-bridge.extra` |
| `triggers`, `permissions`, `subagent` | Devin | parked in `metadata.skill-bridge.extra`; `triggers: [model]` is model-only and maps to `user-invocable: false`, `triggers: [user]` to `disable-model-invocation: true` |
| `metadata.hermes`, `metadata.continue`, `metadata.opencode` | the owning harness | kept as-is in `metadata` |

`metadata.skill-bridge.extra` is read back out when a skill reaches a harness
that *can* express those fields, so `Claude Code → Codex → Gemini → Claude Code`
returns the original `allowed-tools`, `model` and `argument-hint`. Each output
therefore carries only keys its harness actually reads — which matters because
claude.ai uploads reject unknown frontmatter keys rather than ignoring them.

Invocation intent is carried in `metadata.skill-bridge.manual-invocation` too,
so `Devin (triggers: [model]) → Codex → Claude Code` still comes back as
`user-invocable: false` instead of quietly becoming user-invocable again. Run
`skill-bridge validate <skill-dir> --harness <fmt>` to see which fields that
harness cannot express natively, and add `--fail-on-lossy` to make it a CI
error.

Source detection is just as broad: skills are attributed to a harness by any of
its install layouts, including the user-level ones (`~/.config/devin/skills`,
`~/.codeium/windsurf/skills`, `~/.config/opencode/skills`, `~/.clinerules/skills`)
and by frontmatter hints when a skill has been copied out of its harness folder.

## Programmatic API

```python
from pathlib import Path
from skill_bridge.adopt import run_adopt
from skill_bridge.sync import reverse_convert
from skill_bridge.models import AgentFormat

# Every other harness -> mine
report = run_adopt(AgentFormat.HERMES)
print(len(report.selected()), "skills imported")

# One harness -> another
results = reverse_convert(AgentFormat.GEMINI, AgentFormat.CURSOR)
for r in results:
    print(r.skill_name, r.output_dir, r.warnings)
```

## Development

```bash
pip install -e ".[all]"
pytest
python scripts/e2e_all_harnesses.py   # seeds every harness, installs into each
```

The suite includes `tests/test_matrix.py`, which converts a skill from **every** harness format to **every** harness format (16 × 16 = 256 combinations) and asserts the name, description, body, references, provenance marker, and spec validity survive.

## License

MIT — see [LICENSE](LICENSE).
