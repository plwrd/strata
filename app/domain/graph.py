"""Graph domain model.

Graph *extraction* lives in Python; graph *rendering* lives in TypeScript. The
two never mix (ADR-0010). This module defines the wire shape between them.

Privacy: a node belonging to a locked layer is emitted as a redacted placeholder
(``locked=True``, ``label="Locked knowledge object"``) and carries no title, no
tags, no properties and no folder. Edges touching it keep their endpoints — the
existence of *a* link is already visible from the public side — but a sensitive
relationship label is downgraded to ``related_to``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NodeType = Literal[
    "note",
    "folder",
    "tag",
    "person",
    "project",
    "task",
    "attachment",
    "concept",
    "source",
    "decision",
    "cluster",
    "view",
]

EdgeType = Literal[
    "link",
    "backlink",
    "relationship",
    "folder_membership",
    "tag_membership",
    "semantic_similarity",
    "task_dependency",
    "citation",
    "contradiction",
    "version_lineage",
]

EdgeOrigin = Literal["explicit", "derived", "ai-suggested"]

LOCKED_NODE_LABEL = "Locked knowledge object"


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    layer_id: str
    type: NodeType = "note"
    label: str
    locked: bool = False
    folder_path: str = ""
    tags: list[str] = Field(default_factory=list)
    degree: int = 0
    updated_at: str = ""
    word_count: int = 0

    @classmethod
    def redacted(cls, node_id: str, layer_id: str) -> GraphNode:
        """A node in a locked layer: shape only, no content."""
        return cls(id=node_id, layer_id=layer_id, type="note", label=LOCKED_NODE_LABEL, locked=True)


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    type: EdgeType = "link"
    relationship: str = "references"
    origin: EdgeOrigin = "explicit"
    confidence: float | None = None
    weight: float = 1.0


class GraphSnapshot(BaseModel):
    """A whole graph payload as sent to the renderer."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    truncated: bool = False
    total_nodes: int = 0
    total_edges: int = 0
    locked_layer_ids: list[str] = Field(default_factory=list)
