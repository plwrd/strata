"""Graph extraction.

Turns notes, folders, tags and links into a :class:`GraphSnapshot`. Rendering is
somebody else's job (ADR-0010): nothing in this module knows about pixels.

Privacy behaviour is implemented here rather than in the renderer, because a
renderer that receives a private title has already leaked it. Nodes in locked
layers are emitted redacted, or omitted entirely when even their existence would
be informative.
"""

from __future__ import annotations

from app.domain.graph import GraphEdge, GraphNode, GraphSnapshot, NodeType
from app.domain.note import Note
from app.services.note_service import NoteService
from app.services.workspace_service import WorkspaceService

# A relationship label can itself be sensitive ("blocks", "contradicts" between a
# public and a private note). Cross-boundary edges into locked layers are
# downgraded to the neutral label.
NEUTRAL_RELATIONSHIP = "related_to"

DEFAULT_NODE_LIMIT = 20_000


class GraphMode:
    WORKSPACE = "workspace"
    LAYER = "layer"
    FOLDER = "folder"
    LOCAL = "local"
    SELECTION = "selection"


class GraphService:
    def __init__(self, workspace: WorkspaceService, notes: NoteService) -> None:
        self._workspace = workspace
        self._notes = notes

    def build(
        self,
        *,
        layer_ids: list[str] | None = None,
        include_tags: bool = True,
        include_folders: bool = True,
        focus_note_id: str | None = None,
        neighbour_depth: int = 1,
        node_limit: int = DEFAULT_NODE_LIMIT,
    ) -> GraphSnapshot:
        notes = self._notes.list_notes(layer_ids)
        title_index = NoteService.build_title_index(notes)

        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        for note in notes:
            meta = note.metadata
            nodes[meta.id] = GraphNode(
                id=meta.id,
                layer_id=meta.layer_id,
                type=self._node_type_for(note),
                label=meta.title,
                folder_path=meta.folder_path,
                tags=meta.tags,
                updated_at=meta.updated_at,
                word_count=meta.word_count,
            )

        if include_folders:
            for folder in self._notes.list_folders(layer_ids):
                nodes[folder.id] = GraphNode(
                    id=folder.id,
                    layer_id=folder.layer_id,
                    type="folder",
                    label=folder.name,
                    folder_path=folder.path,
                )
            for note in notes:
                folder_id = self._folder_id_for(note)
                if folder_id and folder_id in nodes:
                    edges.append(
                        GraphEdge(
                            id=f"e_folder_{folder_id}_{note.metadata.id}",
                            source=folder_id,
                            target=note.metadata.id,
                            type="folder_membership",
                            relationship="contains",
                            origin="derived",
                            weight=0.4,
                        )
                    )

        if include_tags:
            for note in notes:
                for tag in note.metadata.tags:
                    tag_id = f"tag:{tag}"
                    if tag_id not in nodes:
                        nodes[tag_id] = GraphNode(
                            id=tag_id,
                            layer_id=note.metadata.layer_id,
                            type="tag",
                            label=f"#{tag}",
                        )
                    edges.append(
                        GraphEdge(
                            id=f"e_tag_{tag_id}_{note.metadata.id}",
                            source=note.metadata.id,
                            target=tag_id,
                            type="tag_membership",
                            relationship="tagged",
                            origin="derived",
                            weight=0.3,
                        )
                    )

        for note in notes:
            for link in note.metadata.links:
                target_id = title_index.get(link.target_title.strip().lower())
                if target_id is None or target_id == note.metadata.id:
                    continue  # broken links are surfaced in the editor, not the graph
                edges.append(
                    GraphEdge(
                        id=f"e_link_{note.metadata.id}_{target_id}_{link.relationship}",
                        source=note.metadata.id,
                        target=target_id,
                        type="relationship" if link.relationship != "references" else "link",
                        relationship=link.relationship,
                        origin="explicit",
                    )
                )

        locked = self._workspace.locked_layers()
        for layer in locked:
            # A locked layer contributes a single redacted marker so the user can
            # see that knowledge exists without learning anything about it. It
            # contributes no titles, no counts and no edges.
            node_id = f"locked:{layer.id}"
            nodes[node_id] = GraphNode.redacted(node_id, layer.id)

        if focus_note_id:
            nodes, edges = self._restrict_to_neighbourhood(
                nodes, edges, focus_note_id, neighbour_depth
            )

        degree: dict[str, int] = {}
        for edge in edges:
            degree[edge.source] = degree.get(edge.source, 0) + 1
            degree[edge.target] = degree.get(edge.target, 0) + 1
        for node_id, node in nodes.items():
            node.degree = degree.get(node_id, 0)

        total_nodes = len(nodes)
        total_edges = len(edges)
        truncated = total_nodes > node_limit
        if truncated:
            kept = dict(
                sorted(nodes.items(), key=lambda item: item[1].degree, reverse=True)[:node_limit]
            )
            edges = [e for e in edges if e.source in kept and e.target in kept]
            nodes = kept

        return GraphSnapshot(
            nodes=list(nodes.values()),
            edges=edges,
            truncated=truncated,
            total_nodes=total_nodes,
            total_edges=total_edges,
            locked_layer_ids=[layer.id for layer in locked],
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _node_type_for(note: Note) -> NodeType:
        """Map the note's declared schema onto a graph node type.

        The `type` property is user data, so an unrecognised value degrades to a
        plain note rather than being trusted as a node type.
        """
        declared = note.metadata.properties.get("type")
        mapping: dict[str, NodeType] = {
            "person": "person",
            "project": "project",
            "task": "task",
            "decision": "decision",
            "source": "source",
            "research-source": "source",
            "concept": "concept",
            "architecture-component": "concept",
            "security-threat": "concept",
        }
        if isinstance(declared, str):
            return mapping.get(declared.strip().lower(), "note")
        return "note"

    @staticmethod
    def _folder_id_for(note: Note) -> str | None:
        if not note.metadata.folder_path:
            return None
        from app.infrastructure.storage.markdown_store import note_id_for

        return note_id_for(note.metadata.layer_id, note.metadata.folder_path + "/")

    @staticmethod
    def _restrict_to_neighbourhood(
        nodes: dict[str, GraphNode],
        edges: list[GraphEdge],
        focus_id: str,
        depth: int,
    ) -> tuple[dict[str, GraphNode], list[GraphEdge]]:
        if focus_id not in nodes:
            return nodes, edges
        frontier = {focus_id}
        visited = {focus_id}
        for _ in range(max(0, depth)):
            neighbours: set[str] = set()
            for edge in edges:
                if edge.source in frontier:
                    neighbours.add(edge.target)
                if edge.target in frontier:
                    neighbours.add(edge.source)
            frontier = neighbours - visited
            visited |= neighbours
            if not frontier:
                break
        kept_nodes = {node_id: node for node_id, node in nodes.items() if node_id in visited}
        kept_edges = [e for e in edges if e.source in visited and e.target in visited]
        return kept_nodes, kept_edges

    def neighbours(self, note_id: str, snapshot: GraphSnapshot) -> list[str]:
        result: list[str] = []
        for edge in snapshot.edges:
            if edge.source == note_id and edge.target not in result:
                result.append(edge.target)
            elif edge.target == note_id and edge.source not in result:
                result.append(edge.source)
        return result
