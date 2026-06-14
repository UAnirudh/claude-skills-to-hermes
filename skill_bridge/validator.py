"""Validate skills against the Agent Skills open specification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from skill_bridge.models import SkillBundle

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass
class ValidationIssue:
    level: str  # error | warning
    field: str
    message: str


@dataclass
class ValidationReport:
    skill_name: str
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "error"]

    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "warning"]


def validate_skill(bundle: SkillBundle, *, strict_dir_name: bool = True) -> ValidationReport:
    report = ValidationReport(skill_name=bundle.name)
    name = bundle.name

    if not name:
        report.issues.append(ValidationIssue("error", "name", "name is required"))
    elif len(name) > 64:
        report.issues.append(ValidationIssue("error", "name", "name must be at most 64 characters"))
    elif not NAME_RE.match(name):
        report.issues.append(
            ValidationIssue(
                "error",
                "name",
                "name must be lowercase alphanumeric with single hyphens (no leading/trailing hyphen)",
            )
        )

    if strict_dir_name and bundle.root.name != name:
        report.issues.append(
            ValidationIssue(
                "warning",
                "name",
                f"directory name '{bundle.root.name}' should match skill name '{name}'",
            )
        )

    desc = bundle.description
    if not desc:
        report.issues.append(ValidationIssue("error", "description", "description is required"))
    elif len(desc) > 1024:
        report.issues.append(
            ValidationIssue("error", "description", "description must be at most 1024 characters")
        )
    elif len(desc) < 20:
        report.issues.append(
            ValidationIssue(
                "warning",
                "description",
                "description is very short; include WHAT and WHEN trigger terms",
            )
        )

    line_count = len(bundle.body.splitlines())
    if line_count > 500:
        report.issues.append(
            ValidationIssue(
                "warning",
                "body",
                f"SKILL.md body is {line_count} lines; consider splitting into references/",
            )
        )

    compatibility = bundle.frontmatter.get("compatibility")
    if compatibility and len(str(compatibility)) > 500:
        report.issues.append(
            ValidationIssue("error", "compatibility", "compatibility must be at most 500 characters")
        )

    return report
