"""Workspace lifecycle, Markdown parsing, and graph extraction."""

from __future__ import annotations

import pytest

from app.domain.errors import InvalidRequestError, NotFoundError
from app.domain.note import extract_links, extract_tags
from app.infrastructure.storage.markdown_store import parse_frontmatter, render_frontmatter
from app.services.container import Services


def test_creating_a_workspace_writes_a_descriptor_and_a_layer(services: Services) -> None:
    descriptor = services.workspace.create(
        services.paths.default_workspace, "My Workspace", seed_demo=True
    )

    assert descriptor.name == "My Workspace"
    assert len(descriptor.layers) == 1
    assert (services.paths.default_workspace / "workspace.json").is_file()
    assert list((services.paths.default_workspace / "layers").iterdir())


def test_reopening_a_workspace_preserves_it(services: Services) -> None:
    services.workspace.create(services.paths.default_workspace, "Persisted", seed_demo=True)
    note_count = len(services.notes.list_notes())
    services.workspace.close()

    reopened = services.workspace.open(services.paths.default_workspace)

    assert reopened.name == "Persisted"
    assert len(services.notes.list_notes()) == note_count


def test_opening_a_missing_workspace_is_not_found(services: Services) -> None:
    with pytest.raises(NotFoundError):
        services.workspace.open(services.paths.default_workspace / "nope")


def test_creating_over_an_existing_workspace_is_refused(services: Services) -> None:
    services.workspace.create(services.paths.default_workspace, "One")

    with pytest.raises(InvalidRequestError):
        services.workspace.create(services.paths.default_workspace, "Two")


def test_a_private_layer_needs_a_password(workspace: Services) -> None:
    with pytest.raises(InvalidRequestError):
        workspace.workspace.create_layer("Secrets", visibility="private")


def test_a_private_layer_is_created_unlocked_with_a_recovery_key(workspace: Services) -> None:
    layer, recovery = workspace.workspace.create_layer(
        "Secrets", visibility="private", password="correct horse battery"
    )

    assert layer.visibility == "private"
    assert layer.storage == "encrypted-objects"
    assert layer.state == "unlocked"  # the user just proved they hold the password
    assert recovery is not None
    # A private layer must not default to sending content to a remote model.
    assert layer.ai_policy.access == "local-only"


def test_reordering_requires_every_layer(workspace: Services) -> None:
    with pytest.raises(InvalidRequestError):
        workspace.workspace.reorder_layers(["layer_does_not_exist"])


# --- markdown -------------------------------------------------------------


def test_frontmatter_round_trips() -> None:
    rendered = render_frontmatter({"type": "decision", "tags": ["a", "b"]})
    frontmatter, body = parse_frontmatter(rendered + "Body text.")

    assert frontmatter["type"] == "decision"
    assert frontmatter["tags"] == ["a", "b"]
    assert body == "Body text."


def test_a_document_without_frontmatter_is_all_body() -> None:
    frontmatter, body = parse_frontmatter("# Heading\n\nText.")
    assert frontmatter == {}
    assert body.startswith("# Heading")


def test_wiki_links_are_extracted_with_aliases() -> None:
    links = extract_links("See [[Threat Model]] and [[Encryption Architecture|the crypto]].")

    assert [link.target_title for link in links] == ["Threat Model", "Encryption Architecture"]
    assert links[1].alias == "the crypto"
    assert all(link.relationship == "references" for link in links)


def test_typed_relationships_are_extracted() -> None:
    links = extract_links("supports:: [[Strata Overview]]\ncontradicts:: [[Marketing Claims]]\n")

    by_target = {link.target_title: link.relationship for link in links}
    assert by_target["Strata Overview"] == "supports"
    assert by_target["Marketing Claims"] == "contradicts"


def test_an_unknown_relationship_prefix_is_not_trusted() -> None:
    links = extract_links("pwned:: [[Strata Overview]]")
    assert links[0].relationship == "references"


def test_link_targets_with_headings_and_blocks_resolve_to_the_note() -> None:
    links = extract_links("[[Threat Model#Assets]] and [[Threat Model^block]]")
    assert {link.target_title for link in links} == {"Threat Model"}


def test_tags_combine_frontmatter_and_inline_without_duplicates() -> None:
    tags = extract_tags("Body with #security and #security again.", ["security", "encryption"])
    assert tags == ["security", "encryption"]


# --- graph ----------------------------------------------------------------


def test_graph_contains_notes_folders_and_tags(workspace: Services) -> None:
    snapshot = workspace.graph.build()
    types = {node.type for node in snapshot.nodes}

    assert "note" in types
    assert "folder" in types
    assert "tag" in types


def test_graph_can_omit_tags_and_folders(workspace: Services) -> None:
    snapshot = workspace.graph.build(include_tags=False, include_folders=False)
    types = {node.type for node in snapshot.nodes}

    assert "tag" not in types
    assert "folder" not in types


def test_node_type_follows_the_declared_schema(workspace: Services) -> None:
    snapshot = workspace.graph.build()
    labels = {node.label: node.type for node in snapshot.nodes}

    # `type: decision` in frontmatter becomes a decision node.
    assert labels["Marketing Claims"] == "decision"
    # `type: architecture-component` maps onto the concept node type.
    assert labels["Encryption Architecture"] == "concept"


def test_degree_is_computed(workspace: Services) -> None:
    snapshot = workspace.graph.build()
    overview = next(node for node in snapshot.nodes if node.label == "Strata Overview")
    assert overview.degree > 0


def test_broken_links_do_not_create_edges(workspace: Services) -> None:
    layer_id = workspace.workspace.descriptor.layers[0].id
    workspace.notes.create_note(
        layer_id=layer_id,
        folder_path="",
        title="Dangling",
        content="This points at [[A Note That Does Not Exist]].",
    )

    snapshot = workspace.graph.build()
    dangling = next(node for node in snapshot.nodes if node.label == "Dangling")
    link_edges = [
        edge
        for edge in snapshot.edges
        if edge.source == dangling.id and edge.type in ("link", "relationship")
    ]

    assert link_edges == []


def test_local_graph_restricts_to_a_neighbourhood(workspace: Services) -> None:
    full = workspace.graph.build(include_tags=False, include_folders=False)
    focus = next(node for node in full.nodes if node.label == "Marketing Claims")

    local = workspace.graph.build(
        include_tags=False, include_folders=False, focus_note_id=focus.id, neighbour_depth=1
    )

    assert len(local.nodes) < len(full.nodes)
    assert focus.id in {node.id for node in local.nodes}


def test_node_limit_truncates_and_says_so(workspace: Services) -> None:
    snapshot = workspace.graph.build(node_limit=3)

    assert snapshot.truncated is True
    assert len(snapshot.nodes) == 3
    assert snapshot.total_nodes > 3
    # Every surviving edge must still connect two surviving nodes.
    ids = {node.id for node in snapshot.nodes}
    assert all(edge.source in ids and edge.target in ids for edge in snapshot.edges)
