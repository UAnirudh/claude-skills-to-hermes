# Skill Bridge

[![CI](https://github.com/YOUR_USERNAME/skill-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_USERNAME/skill-bridge/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/spec-agentskills.io-green.svg)](https://agentskills.io)

**Convert and sync Agent Skills** across Claude Code, Hermes Agent, OpenAI Codex, Cursor, Windsurf, Cline, Roo Code, and the [agentskills.io](https://agentskills.io) open standard — author once, deploy everywhere.

## Why Skill Bridge?

Every major coding agent now uses the same core format: a folder with `SKILL.md` (YAML frontmatter + Markdown) plus optional `scripts/`, `references/`, and `assets/`. The differences are install paths and small metadata conventions. Skill Bridge handles all of that.

| Feature | Command |
|---------|---------|
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
skill-bridge init
skill-bridge sync
```

## Supported agents

| Format ID | Agent | Global path | Project path |
|-----------|-------|-------------|--------------|
| `claude-code` | Claude Code | `~/.claude/skills` | `.claude/skills` |
| `cursor` | Cursor | `~/.cursor/skills` | `.cursor/skills` |
| `hermes` | Hermes Agent | `~/.hermes/skills` | `.hermes/skills` |
| `codex` | OpenAI Codex | `~/.agents/skills` | `.agents/skills` |
| `windsurf` | Windsurf | `~/.windsurf/skills` | `.windsurf/skills` |
| `cline` | Cline | `~/.cline/skills` | `.cline/skills` |
| `roo` | Roo Code | `~/.roo/skills` | `.roo/skills` |
| `continue` | Continue.dev | `~/.continue/skills` | `.continue/skills` |

Run `skill-bridge list-formats` for the full list.

## Configuration

Run `skill-bridge init` to generate `skill-bridge.yaml`, or copy the example:

```bash
cp skill-bridge.yaml.example skill-bridge.yaml
```

Example profile:

```yaml
default_profile: default

profiles:
  default:
    source:
      format: claude-code
      path: ~/.claude/skills
    targets:
      - format: hermes
        path: ~/.hermes/skills
      - format: codex
        path: ~/.agents/skills
      - format: cursor
        path: ~/.cursor/skills
    watch: true
    validate: true
```

Then:

```bash
skill-bridge sync              # sync all skills once
skill-bridge watch             # re-sync on file changes
skill-bridge sync --skill my-skill   # sync one skill
```

## Reverse conversion

Pull skills from any agent into another:

```bash
# Hermes community skills -> Claude Code
skill-bridge reverse --from hermes --to claude-code

# Codex skills -> Cursor (project scope)
skill-bridge reverse --from codex --to cursor --scope project

# Custom paths
skill-bridge reverse --from hermes --to claude-code \
  --from-path ~/Downloads/hermes-skills \
  --to-path ~/.claude/skills
```

Or use the `hermes-to-claude` profile in `skill-bridge.yaml.example`:

```bash
skill-bridge sync --profile hermes-to-claude
```

## CLI reference

```
skill-bridge init [--source claude-code] [--target hermes] [--target codex]
skill-bridge detect                    # find installed agent skill dirs
skill-bridge list-formats
skill-bridge inspect <skill-dir>
skill-bridge validate <skill-dir>
skill-bridge convert <path> --to <format> [--to ...] [-o output]
skill-bridge reverse --from <fmt> --to <fmt> [--scope global|project]
skill-bridge sync [--profile NAME] [--skill NAME]
skill-bridge watch [--profile NAME] [--debounce 1.0]
skill-bridge batch <root> --all-agents | --to <format>
skill-bridge paths <format> [--scope global|project]
```

## What gets converted

- **SKILL.md** — normalized frontmatter (`name`, `description`, spec-compliant slugs)
- **Body** — preserved verbatim; Hermes adds `## When to Use` if missing
- **Supporting files** — `scripts/`, `references/`, `assets/` copied as-is
- **Agent metadata** — Hermes `metadata.hermes`, Codex `agents/openai.yaml`, Cursor `disable-model-invocation`

## Programmatic API

```python
from pathlib import Path
from skill_bridge.sync import reverse_convert
from skill_bridge.models import AgentFormat

results = reverse_convert(AgentFormat.HERMES, AgentFormat.CLAUDE_CODE)
for r in results:
    print(r.skill_name, r.output_dir, r.warnings)
```

## Development

```bash
pip install -e ".[all]"
pytest
```

## Publish to GitHub

```bash
git init
git add .
git commit -m "Initial commit: Skill Bridge v1.1.0"
gh repo create skill-bridge --public --source=. --push
```

Replace `YOUR_USERNAME` in the README badge URLs after creating the repo.

## License

MIT — see [LICENSE](LICENSE).
