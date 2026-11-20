"""Which frontmatter fields each harness reads, and how to translate between them.

The Agent Skills spec defines six portable fields: ``name``, ``description``,
``license``, ``compatibility``, ``metadata`` and ``allowed-tools``. Everything
else in a SKILL.md is a harness extra: Claude Code's ``allowed-tools`` /
``model`` / ``paths``, Cursor's ``disable-model-invocation`` / ``icon`` /
``color``, Devin's ``triggers`` / ``permissions`` and so on.

Two naive strategies are both wrong:

* copy every foreign key into every output — leaves each harness carrying keys
  it does not read, and some frontends (claude.ai upload, the Skills API) reject
  a skill outright instead of ignoring unknown keys;
* drop extras — loses the author's intent, so a skill converted Claude Code ->
  Codex -> Claude Code comes back with no tool pre-approvals.

This module keeps one table of what each harness actually reads (from its own
docs) and translates the remainder into the target's nearest equivalent. Fields
the target cannot express are parked in ``metadata.skill-bridge.extra``, and a
later conversion to a harness that *can* express them pulls them back out to the
top level. That makes conversions between any two harnesses non-destructive
even when they travel through a harness with fewer fields.
"""

from __future__ import annotations

from typing import Any

from skill_bridge.models import AgentFormat, SkillBundle

#: Metadata namespace used to carry intent a harness cannot express natively.
CARRIER = "skill-bridge"

#: The six fields of the agentskills.io spec. Safe in every harness.
SPEC_FIELDS: frozenset[str] = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)

#: Extra frontmatter keys each harness documents reading, beyond name/description.
#:
#: `license`/`compatibility`/`metadata` are spec fields and are written for
#: every harness even where a loader ignores them: they cost nothing and are
#: the portable half of the format.
NATIVE_FIELDS: dict[AgentFormat, frozenset[str]] = {
    # Claude Code documents ~20 keys: allowed-tools, disallowed-tools, model,
    # effort, context, agent, hooks, paths, shell, argument-hint, user-invocable,
    # disable-model-invocation, when_to_use, arguments, background.
    # ``origin`` is carried for ECC-style skill catalogs.
    AgentFormat.CLAUDE_CODE: frozenset(
        {
            "license",
            "compatibility",
            "metadata",
            "origin",
            "allowed-tools",
            "disallowed-tools",
            "disable-model-invocation",
            "user-invocable",
            "argument-hint",
            "arguments",
            "context",
            "agent",
            "hooks",
            "model",
            "effort",
            "paths",
            "shell",
            "when_to_use",
            "background",
        }
    ),
    # Cursor reads name/description, then paths, disable-model-invocation,
    # icon, color and metadata.
    AgentFormat.CURSOR: frozenset(
        {
            "license",
            "compatibility",
            "metadata",
            "paths",
            "disable-model-invocation",
            "icon",
            "color",
        }
    ),
    # Codex documents name/description only; optional extras go to
    # agents/openai.yaml, not SKILL.md frontmatter.
    AgentFormat.CODEX: frozenset({"license", "compatibility", "metadata"}),
    # Hermes adds version, platforms and its metadata.hermes block
    # (tags, category, requires_toolsets, config).
    AgentFormat.HERMES: frozenset(
        {"license", "compatibility", "metadata", "version", "platforms"}
    ),
    # Devin Desktop's own reference: argument-hint, model, subagent, agent,
    # allowed-tools (list), permissions, triggers.
    AgentFormat.DEVIN: frozenset(
        {
            "license",
            "compatibility",
            "metadata",
            "allowed-tools",
            "argument-hint",
            "model",
            "agent",
            "subagent",
            "permissions",
            "triggers",
        }
    ),
    # OpenCode interprets metadata.opencode.autoinvoke and a truthy
    # disable-model-invocation.
    AgentFormat.OPENCODE: frozenset(
        {"license", "compatibility", "metadata", "disable-model-invocation"}
    ),
    # Copilot reads the spec fields plus user-invocable, argument-hint and
    # disable-model-invocation.
    AgentFormat.COPILOT: frozenset(
        {
            "license",
            "compatibility",
            "metadata",
            "user-invocable",
            "argument-hint",
            "disable-model-invocation",
        }
    ),
    # Gemini CLI, Cline, Roo Code, Continue, Goose, OpenHands, Windsurf,
    # Amp/Zed/Kilo/Warp and the bare standard read the spec fields only.
    AgentFormat.GEMINI: frozenset({"license", "compatibility", "metadata"}),
    AgentFormat.CLINE: frozenset({"license", "compatibility", "metadata"}),
    AgentFormat.ROO: frozenset({"license", "compatibility", "metadata"}),
    AgentFormat.CONTINUE: frozenset({"license", "compatibility", "metadata"}),
    AgentFormat.GOOSE: frozenset({"license", "compatibility", "metadata"}),
    AgentFormat.OPENHANDS: frozenset({"license", "compatibility", "metadata"}),
    AgentFormat.WINDSURF: frozenset({"license", "compatibility", "metadata"}),
    AgentFormat.AMP: frozenset({"license", "compatibility", "metadata"}),
    AgentFormat.OPENAGENTS: frozenset({"license", "compatibility", "metadata"}),
    AgentFormat.GENERIC: frozenset({"license", "compatibility", "metadata"}),
}


def native_fields(fmt: AgentFormat) -> frozenset[str]:
    """Frontmatter keys ``fmt`` documents reading (plus the portable core)."""
    return NATIVE_FIELDS.get(fmt, frozenset({"license", "compatibility", "metadata"}))


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "on", "1"}
    return bool(value)


def as_tool_list(value: Any) -> list[str]:
    """Normalize ``allowed-tools`` (space/comma string or YAML list) to a list."""
    if isinstance(value, str):
        return [part for part in value.replace(",", " ").split() if part]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)] if value else []


def manual_invocation(frontmatter: dict[str, Any]) -> bool | None:
    """Whether a skill is meant to be user-invoked only.

    Returns ``None`` when the source says nothing about invocation, so callers
    do not have to invent a default for harnesses that have no such field.
    """
    if "disable-model-invocation" in frontmatter:
        return truthy(frontmatter["disable-model-invocation"])

    if "user-invocable" in frontmatter and not truthy(frontmatter["user-invocable"]):
        # Model-only invocation: the mirror image of manual-only.
        return False

    triggers = frontmatter.get("triggers")
    if isinstance(triggers, (list, tuple)) and triggers:
        named = {str(t).strip().lower() for t in triggers}
        return "model" not in named

    meta = frontmatter.get("metadata")
    if isinstance(meta, dict):
        opencode = meta.get("opencode")
        if isinstance(opencode, dict) and "autoinvoke" in opencode:
            return not truthy(opencode["autoinvoke"])
    return None


def _coerce(key: str, value: Any, target: AgentFormat) -> Any:
    """Shape a shared field the way ``target`` expects it."""
    if key == "allowed-tools":
        tools = as_tool_list(value)
        if target is AgentFormat.DEVIN:
            return tools  # Devin documents a YAML list
        return " ".join(tools)  # spec/Claude form: space-separated string
    if key in {"disable-model-invocation", "user-invocable"}:
        return truthy(value)
    return value


def adapt_frontmatter(
    bundle: SkillBundle,
    name: str,
    *,
    target: AgentFormat,
) -> dict[str, Any]:
    """Build frontmatter for ``target`` that converts cleanly between harnesses."""
    source = bundle.frontmatter
    native = native_fields(target)

    raw_meta = source.get("metadata")
    meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    raw_carrier = meta.pop(CARRIER, None)
    carrier: dict[str, Any] = dict(raw_carrier) if isinstance(raw_carrier, dict) else {}
    raw_extra = carrier.pop("extra", None)
    parked: dict[str, Any] = dict(raw_extra) if isinstance(raw_extra, dict) else {}

    # Every meaningful source field, plus anything a previous hop parked here.
    extras: dict[str, Any] = {}
    for key, value in source.items():
        if key in {"name", "description", "metadata"} or value is None:
            continue
        extras[key] = value
    for key, value in parked.items():
        if value is not None:
            extras.setdefault(key, value)

    frontmatter: dict[str, Any] = {
        "name": name,
        "description": bundle.description[:1024],
    }

    unexpressible: dict[str, Any] = {}
    for key in sorted(extras):
        if key in native:
            frontmatter[key] = _coerce(key, extras[key], target)
        else:
            unexpressible[key] = extras[key]

    invocation = manual_invocation(source)
    if invocation is None and isinstance(carrier.get("manual-invocation"), bool):
        invocation = carrier["manual-invocation"]

    if invocation is True:
        if "disable-model-invocation" in native:
            frontmatter["disable-model-invocation"] = True
            carrier.pop("manual-invocation", None)
        else:
            carrier["manual-invocation"] = True
            if target is AgentFormat.HERMES:
                hermes = meta.get("hermes")
                hermes = dict(hermes) if isinstance(hermes, dict) else {}
                hermes.setdefault("category", "workflow")
                meta["hermes"] = hermes
    elif invocation is False:
        # Model-only (Devin ``triggers: [model]``, Claude ``user-invocable:
        # false``): the harness must hide it from the user, so the default here
        # is False, not True.
        carrier.pop("manual-invocation", None)
        if "user-invocable" in native:
            frontmatter.setdefault("user-invocable", False)
    else:
        carrier.pop("manual-invocation", None)

    if unexpressible:
        carrier["extra"] = unexpressible
    else:
        carrier.pop("extra", None)

    if carrier:
        meta[CARRIER] = carrier
    else:
        meta.pop(CARRIER, None)
    if meta:
        frontmatter["metadata"] = meta

    return frontmatter
