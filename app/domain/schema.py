"""Property types and reusable note schemas.

A schema is a *description*, not a database table: it says what properties a
"Meeting" usually has, validates them, and drives the property editor. The
Markdown file is still the source of truth, and a note that violates its schema
is reported, never rewritten or rejected — the user's file always wins.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PropertyType = Literal[
    "text",
    "number",
    "boolean",
    "date",
    "datetime",
    "tags",
    "relation",
    "url",
    "email",
    "select",
    "multi-select",
    "formula",
    "status",
    "person",
    "location",
    "duration",
    "rating",
    "progress",
]


class PropertyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str = ""
    type: PropertyType = "text"
    required: bool = False
    default: Any = None
    options: list[str] = Field(default_factory=list)  # select / multi-select / status
    minimum: float | None = None
    maximum: float | None = None
    formula: str = ""  # evaluated in Milestone 10; stored, never executed here
    description: str = ""

    def display_label(self) -> str:
        return self.label or self.key.replace("_", " ").title()


class NoteSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    icon: str = "▪"
    node_style: str = "note"  # maps onto a graph node type
    properties: list[PropertyDefinition] = Field(default_factory=list)
    allowed_relationships: list[str] = Field(default_factory=list)
    template: str = ""
    builtin: bool = True


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    problem: str


def validate_properties(schema: NoteSchema, properties: dict[str, Any]) -> list[ValidationIssue]:
    """Report what does not fit. Never mutates, never rejects the file."""
    issues: list[ValidationIssue] = []
    for definition in schema.properties:
        if definition.key not in properties or properties[definition.key] in (None, ""):
            if definition.required:
                issues.append(ValidationIssue(key=definition.key, problem="required"))
            continue

        value = properties[definition.key]
        problem = _check(definition, value)
        if problem:
            issues.append(ValidationIssue(key=definition.key, problem=problem))
    return issues


def _check(definition: PropertyDefinition, value: Any) -> str | None:
    kind = definition.type

    if kind in ("number", "rating", "progress", "duration"):
        # `bool` is a subclass of `int`, so `True` would otherwise pass as a number.
        if not isinstance(value, int | float) or isinstance(value, bool):
            return "expected a number"
        if definition.minimum is not None and value < definition.minimum:
            return f"below the minimum of {definition.minimum}"
        if definition.maximum is not None and value > definition.maximum:
            return f"above the maximum of {definition.maximum}"
        return None

    if kind == "boolean":
        return None if isinstance(value, bool) else "expected true or false"

    if kind in ("date", "datetime"):
        if isinstance(value, date | datetime):
            return None
        try:
            datetime.fromisoformat(str(value))
        except ValueError:
            return "expected an ISO date"
        return None

    if kind in ("tags", "multi-select"):
        if not isinstance(value, list):
            return "expected a list"
        if kind == "multi-select" and definition.options:
            unknown = [v for v in value if str(v) not in definition.options]
            if unknown:
                return f"not an allowed option: {', '.join(map(str, unknown))}"
        return None

    if kind in ("select", "status") and definition.options:
        return None if str(value) in definition.options else "not an allowed option"

    if kind == "url":
        text = str(value)
        return None if text.startswith(("http://", "https://")) else "expected a URL"

    if kind == "email":
        text = str(value)
        return None if "@" in text and "." in text.split("@")[-1] else "expected an email address"

    return None


def _p(key: str, kind: PropertyType, **kwargs: Any) -> PropertyDefinition:
    return PropertyDefinition(key=key, type=kind, **kwargs)


STATUS_OPTIONS = ["not started", "in progress", "blocked", "done", "cancelled"]

BUILTIN_SCHEMAS: list[NoteSchema] = [
    NoteSchema(
        id="meeting",
        name="Meeting",
        icon="◷",
        node_style="note",
        properties=[
            _p("type", "select", options=["meeting"], required=True, default="meeting"),
            _p("date", "datetime", required=True),
            _p("attendees", "multi-select"),
            _p("decisions", "text"),
            _p("follow_up", "boolean", default=False),
        ],
        template="## Agenda\n\n## Notes\n\n## Decisions\n\n## Follow-up\n",
        allowed_relationships=["references", "relates_to", "supersedes"],
    ),
    NoteSchema(
        id="project",
        name="Project",
        icon="▣",
        node_style="project",
        properties=[
            _p("type", "select", options=["project"], required=True, default="project"),
            _p("status", "status", options=STATUS_OPTIONS, required=True, default="in progress"),
            _p("owner", "person"),
            _p("due", "date"),
            _p("progress", "progress", minimum=0, maximum=100, default=0),
        ],
        template="## Goal\n\n## Scope\n\n## Milestones\n\n## Risks\n",
        allowed_relationships=["depends_on", "blocks", "relates_to"],
    ),
    NoteSchema(
        id="person",
        name="Person",
        icon="☺",
        node_style="person",
        properties=[
            _p("type", "select", options=["person"], required=True, default="person"),
            _p("email", "email"),
            _p("role", "text"),
            _p("location", "location"),
        ],
        allowed_relationships=["created_by", "assigned_to", "relates_to"],
    ),
    NoteSchema(
        id="research-source",
        name="Research source",
        icon="❝",
        node_style="source",
        properties=[
            _p(
                "type",
                "select",
                options=["research-source"],
                required=True,
                default="research-source",
            ),
            _p("url", "url"),
            _p("author", "text"),
            _p("published", "date"),
            _p("credibility", "rating", minimum=1, maximum=5),
        ],
        template="## Summary\n\n## Key claims\n\n## Quotes\n\n## Assessment\n",
        allowed_relationships=["evidence_for", "contradicts", "supports"],
    ),
    NoteSchema(
        id="daily-note",
        name="Daily note",
        icon="☀",
        node_style="note",
        properties=[
            _p("type", "select", options=["daily-note"], required=True, default="daily-note"),
            _p("date", "date", required=True),
            _p("mood", "rating", minimum=1, maximum=5),
        ],
        template="## Today\n\n## Notes\n\n## Tomorrow\n",
    ),
    NoteSchema(
        id="task",
        name="Task",
        icon="☐",
        node_style="task",
        properties=[
            _p("type", "select", options=["task"], required=True, default="task"),
            _p("status", "status", options=STATUS_OPTIONS, required=True, default="not started"),
            _p("assignee", "person"),
            _p("due", "date"),
            _p("estimate", "duration"),
        ],
        allowed_relationships=["depends_on", "blocks", "assigned_to"],
    ),
    NoteSchema(
        id="decision",
        name="Decision record",
        icon="◇",
        node_style="decision",
        properties=[
            _p("type", "select", options=["decision"], required=True, default="decision"),
            _p(
                "status",
                "status",
                options=["proposed", "accepted", "rejected", "superseded"],
                required=True,
                default="proposed",
            ),
            _p("date", "date"),
            _p("deciders", "multi-select"),
            _p("review_date", "date", description="When this decision should be revisited."),
        ],
        template=("## Context\n\n## Decision\n\n## Consequences\n\n## Alternatives considered\n"),
        allowed_relationships=["supersedes", "depends_on", "contradicts", "evidence_for"],
    ),
    NoteSchema(
        id="architecture-component",
        name="Architecture component",
        icon="◆",
        node_style="concept",
        properties=[
            _p(
                "type",
                "select",
                options=["architecture-component"],
                required=True,
                default="architecture-component",
            ),
            _p("status", "status", options=["designed", "building", "shipped", "retired"]),
            _p("owner", "person"),
        ],
        template="## Responsibility\n\n## Interfaces\n\n## Dependencies\n\n## Risks\n",
        allowed_relationships=["depends_on", "supports", "expands"],
    ),
    NoteSchema(
        id="security-threat",
        name="Security threat",
        icon="⚠",
        node_style="concept",
        properties=[
            _p(
                "type",
                "select",
                options=["security-threat"],
                required=True,
                default="security-threat",
            ),
            _p(
                "status",
                "status",
                options=["mitigated", "partially mitigated", "accepted", "out of scope"],
                required=True,
            ),
            _p("severity", "rating", minimum=1, maximum=5),
        ],
        template="## Threat\n\n## Mitigation\n\n## Residual risk\n",
        allowed_relationships=["contradicts", "evidence_for", "relates_to"],
    ),
    NoteSchema(
        id="incident",
        name="Incident report",
        icon="✸",
        node_style="note",
        properties=[
            _p("type", "select", options=["incident"], required=True, default="incident"),
            _p("severity", "select", options=["sev1", "sev2", "sev3", "sev4"], required=True),
            _p("detected", "datetime"),
            _p("resolved", "datetime"),
        ],
        template="## Impact\n\n## Timeline\n\n## Root cause\n\n## Actions\n",
    ),
    # -- The knowledge loop (docs/target-architecture.md §2) -------------------
    #
    # Raw material lands as a `capture`, processing promotes it into `concept` /
    # `person` / `organization` pages, synthesis produces `report`s, and
    # `template` / `weekly-review` close the loop. AI-authored pages start at
    # review_status "ai-inferred" and only a human marks them "reviewed".
    NoteSchema(
        id="capture",
        name="Capture",
        icon="⇣",
        node_style="source",
        properties=[
            _p("type", "select", options=["capture"], required=True, default="capture"),
            _p(
                "processing_status",
                "status",
                options=["raw", "processing", "processed", "archived"],
                required=True,
                default="raw",
            ),
            _p("source_url", "url"),
            _p("source_author", "text"),
            _p("published_at", "date"),
            _p("captured_at", "datetime"),
            _p("capture_reason", "text", description="Why this was worth keeping."),
        ],
        allowed_relationships=["references", "evidence_for", "relates_to"],
    ),
    NoteSchema(
        id="concept",
        name="Concept",
        icon="✦",
        node_style="concept",
        properties=[
            _p("type", "select", options=["concept"], required=True, default="concept"),
            _p(
                "review_status",
                "status",
                options=["unverified", "ai-inferred", "reviewed"],
                required=True,
                default="unverified",
            ),
            _p("confidence", "progress", minimum=0, maximum=100),
            _p("last_verified", "date"),
            _p("generated_by", "text", description="AI execution id when AI-authored."),
        ],
        template="## Definition\n\n## Why it matters\n\n## Sources\n\n## Related\n",
        allowed_relationships=["expands", "supports", "contradicts", "relates_to", "derived_from"],
    ),
    NoteSchema(
        id="organization",
        name="Organization",
        icon="▩",
        node_style="person",
        properties=[
            _p("type", "select", options=["organization"], required=True, default="organization"),
            _p("website", "url"),
            _p("industry", "text"),
        ],
        allowed_relationships=["relates_to", "depends_on"],
    ),
    NoteSchema(
        id="report",
        name="Report",
        icon="▤",
        node_style="note",
        properties=[
            _p("type", "select", options=["report"], required=True, default="report"),
            _p(
                "report_kind",
                "select",
                options=[
                    "summary",
                    "comparison",
                    "research-brief",
                    "project-plan",
                    "faq",
                    "timeline",
                    "concept-synthesis",
                ],
            ),
            _p(
                "review_status",
                "status",
                options=["unverified", "ai-inferred", "reviewed"],
                required=True,
                default="ai-inferred",
            ),
            _p("generated_by", "text"),
        ],
        allowed_relationships=["derived_from", "references", "supersedes"],
    ),
    NoteSchema(
        id="template",
        name="Template",
        icon="▦",
        node_style="note",
        properties=[
            _p("type", "select", options=["template"], required=True, default="template"),
            _p("for_schema", "text", description="The schema this template instantiates."),
            _p("template_version", "number", default=1),
        ],
        allowed_relationships=["relates_to"],
    ),
    NoteSchema(
        id="weekly-review",
        name="Weekly review",
        icon="◔",
        node_style="note",
        properties=[
            _p("type", "select", options=["weekly-review"], required=True, default="weekly-review"),
            _p("week_start", "date", required=True),
            _p(
                "review_status",
                "status",
                options=["unverified", "ai-inferred", "reviewed"],
                required=True,
                default="ai-inferred",
            ),
            _p("generated_by", "text"),
        ],
        template=(
            "## Learned\n\n## Decided\n\n## Completed\n\n## Unresolved\n\n"
            "## Emerging themes\n\n## Next\n"
        ),
        allowed_relationships=["references", "derived_from"],
    ),
]

# The default knowledge areas, seeded as folders in a new workspace's first
# public layer. Conventions, not walls: users can rename or ignore them.
KNOWLEDGE_AREAS: tuple[str, ...] = ("Inbox", "Knowledge", "Reports", "Templates")
INBOX_FOLDER = "Inbox"
KNOWLEDGE_FOLDER = "Knowledge"
REPORTS_FOLDER = "Reports"
TEMPLATES_FOLDER = "Templates"


def schema_by_id(schema_id: str) -> NoteSchema | None:
    return next((schema for schema in BUILTIN_SCHEMAS if schema.id == schema_id), None)


def schema_for_note(properties: dict[str, Any]) -> NoteSchema | None:
    """The schema a note declares through its `type` property, if any."""
    declared = properties.get("type")
    if not isinstance(declared, str):
        return None
    return schema_by_id(declared.strip().lower())
