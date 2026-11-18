# Skill Bridge

[![CI](https://github.com/YOUR_USERNAME/skill-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_USERNAME/skill-bridge/actions/workflows/ci.yml)
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
| Live watch mode | `skill-bridge watch` |
| Batch all skills | `skill-bridge batch ~/.claude/skills --all-agents` |

## Quick start (30 seconds)

### Windows

```powershell
git clone https://github.com/YOUR_USERNAME/skill-bridge.git
cd skill-bridge
.\setup.ps1
.\.venv\Scripts\Activate.ps1
skill-bridge detect
```

### macOS / Linux

```bash
git clone https://github.com/YOUR_USERNAME/skill-bridge.git
cd skill-bridge
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
3. writes a `skill-bridge.yaml` profile called `auto-import` so `skill-bridge sync` and `skill-bridge watch` keep them current (`watch` follows every source the profile resolves, including harnesses installed while it runs),
4. records a `.skill-bridge.json` provenance marker in each converted skill.

Useful flags:

```bash
skill-bridge install --harness cursor --dry-run        # preview, write nothing
skill-bridge install --harness claude-code --from codex --from gemini
skill-bridge install --harness opencode --scope project
skill-bridge adopt --to copilot                        # import without writing config
skill-bridge adopt --to cline --force                  # overwrite hand-written skills too
```

Safety rules that make automatic import safe to re-run:

* a skill your harness authored itself is **never** overwritten without `--force`;
* skills converted earlier are refreshed (not duplicated);
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

Skill Bridge writes each harness's documented first-choice location, re-checked against the current docs: [Cline](https://docs.cline.bot/customization/skills), [Devin Desktop](https://docs.devin.ai/desktop/devin-local/customizations), [Cursor](https://cursor.com/docs/skills), [Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills), [Gemini CLI](https://geminicli.com/docs/cli/skills), [Codex](https://developers.openai.com/codex/build-skills), [Roo Code](https://docs.roocode.com/features/skills), [OpenCode](https://opencode.ai/docs/skills), [Amp](https://ampcode.com/docs/customize/skills), [Claude Code](https://code.claude.com/docs/en/skills), [OpenHands](https://docs.openhands.dev/overview/skills) and [Goose](https://goose-docs.ai/docs/guides/context-engineering/using-skills). Cline also reads `.clinerules/skills` and `.claude/skills`. Windsurf is now Devin Desktop: the newer `.devin/skills` / `~/.config/devin/skills` pair is the `devin` row, while the legacy `.windsurf/skills` / `~/.codeium/windsurf/skills` pair the `windsurf` row writes is still read by Cascade, so both layouts work. `.agents/skills` is likewise read by Devin, Cursor, Copilot, Roo, Continue, OpenCode, Goose, OpenHands and Amp. Goose and OpenHands now recommend `.agents/skills` but still discover their own `.goose/skills` / `.openhands/skills` folders, which are written here so existing installs keep updating instead of being duplicated. The shared `.agents/skills` folder remains the portable target (`codex`/`amp`).

Run `skill-bridge list-formats` for the same table with descriptions, and `skill-bridge detect` to see which of them exist on your machine.

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
skill-bridge watch                   # re-sync on file changes
skill-bridge sync --skill my-skill   # sync one skill
```

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
skill-bridge install --harness <fmt> [--from <fmt>] [--scope global|project|both] [--dry-run] [--force]
skill-bridge adopt --to <fmt> [--from <fmt>] [--scope ...] [--dry-run] [--force]
skill-bridge detect                    # every harness + whether it is installed here
skill-bridge list-formats
skill-bridge inspect <skill-dir>
skill-bridge validate <skill-dir>
skill-bridge convert <path> --to <format> [--to ...] [-o output]
skill-bridge reverse --from <fmt> --to <fmt> [--scope global|project]
skill-bridge sync [--profile NAME] [--skill NAME]
skill-bridge watch [--profile NAME] [--debounce 1.0]
skill-bridge batch <root> --all-agents | --to <format>
skill-bridge paths <format> [--scope global|project|both]
```

## What gets converted

- **SKILL.md** — normalized frontmatter (`name`, `description`, spec-compliant slugs) in a deterministic key order, so repeated conversions are byte-identical
- **Body** — preserved verbatim; Hermes adds `## When to Use` if missing (never twice)
- **Harness-specific fields** — preserved: Claude Code `origin`/`allowed-tools`, Cursor `disable-model-invocation`, Continue `metadata.continue`, Hermes `metadata.hermes`
- **Supporting files** — `scripts/`, `references/`, `assets/`, `templates/` copied as-is; `reference/` renamed to `references/`
- **Provenance** — a `.skill-bridge.json` marker in every generated skill, used to refresh safely and never clobber hand-written skills

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
```

The suite includes `tests/test_matrix.py`, which converts a skill from **every** harness format to **every** harness format (16 × 16 = 256 combinations) and asserts the name, description, body, references, provenance marker, and spec validity survive.

## License

MIT — see [LICENSE](LICENSE).
