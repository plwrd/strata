"""Structured views: filter, sort, group — over the live notes, not a database."""

from __future__ import annotations

import pytest

from app.domain.views import ViewConfig, ViewFilter, ViewSort
from app.services.container import Services


def layer_id(workspace: Services) -> str:
    return workspace.workspace.descriptor.layers[0].id


def seed_projects(workspace: Services) -> None:
    layer = layer_id(workspace)
    for title, status, priority in [
        ("Alpha", "in progress", "3"),
        ("Beta", "done", "1"),
        ("Gamma", "in progress", "2"),
        ("Delta", "blocked", "5"),
    ]:
        workspace.notes.create_note(
            layer_id=layer,
            folder_path="Projects",
            title=title,
            content=f"# {title}\n",
            properties={"type": "project", "status": status, "priority": priority},
        )


def config(**kwargs: object) -> ViewConfig:
    base: dict[str, object] = {"id": "view_test", "name": "Test", "type": "table"}
    base.update(kwargs)
    return ViewConfig.model_validate(base)


def test_a_table_view_returns_every_note(workspace: Services) -> None:
    result = workspace.views.run(config())

    assert result.total == len(workspace.notes.list_notes())
    assert result.rows[0].title


def test_filter_by_a_property(workspace: Services) -> None:
    seed_projects(workspace)

    result = workspace.views.run(
        config(filters=[ViewFilter(field="status", operator="equals", value="in progress")])
    )

    assert {row.title for row in result.rows} == {"Alpha", "Gamma"}


def test_filter_by_tag_contains(workspace: Services) -> None:
    result = workspace.views.run(
        config(filters=[ViewFilter(field="tags", operator="contains", value="security")])
    )

    assert result.total >= 1
    assert all("security" in row.tags for row in result.rows)


def test_filter_is_empty(workspace: Services) -> None:
    seed_projects(workspace)

    # Only the four projects carry a `priority`; the seeded demo notes do not.
    with_priority = workspace.views.run(
        config(filters=[ViewFilter(field="priority", operator="is_not_empty")])
    )

    assert with_priority.total == 4
    assert {row.title for row in with_priority.rows} == {"Alpha", "Beta", "Gamma", "Delta"}

    without_priority = workspace.views.run(
        config(filters=[ViewFilter(field="priority", operator="is_empty")])
    )
    assert "Alpha" not in {row.title for row in without_priority.rows}


def test_numeric_filter(workspace: Services) -> None:
    seed_projects(workspace)

    result = workspace.views.run(
        config(filters=[ViewFilter(field="priority", operator="greater_than", value="2")])
    )

    assert {row.title for row in result.rows} == {"Alpha", "Delta"}


def test_sort_ascending_and_descending(workspace: Services) -> None:
    seed_projects(workspace)

    ascending = workspace.views.run(
        config(
            filters=[ViewFilter(field="type", operator="equals", value="project")],
            sort=[ViewSort(field="priority", direction="asc")],
        )
    )
    descending = workspace.views.run(
        config(
            filters=[ViewFilter(field="type", operator="equals", value="project")],
            sort=[ViewSort(field="priority", direction="desc")],
        )
    )

    assert [row.title for row in ascending.rows] == ["Beta", "Gamma", "Alpha", "Delta"]
    assert [row.title for row in descending.rows] == ["Delta", "Alpha", "Gamma", "Beta"]


def test_grouping_produces_kanban_columns(workspace: Services) -> None:
    seed_projects(workspace)

    result = workspace.views.run(
        config(
            type="kanban",
            filters=[ViewFilter(field="type", operator="equals", value="project")],
            group_by="status",
        )
    )

    groups = {group.key: [row.title for row in group.rows] for group in result.groups}
    assert set(groups["in progress"]) == {"Alpha", "Gamma"}
    assert groups["done"] == ["Beta"]
    assert groups["blocked"] == ["Delta"]


def test_ungrouped_rows_land_in_a_no_value_bucket(workspace: Services) -> None:
    seed_projects(workspace)

    result = workspace.views.run(config(group_by="status"))

    no_value = next(group for group in result.groups if group.key == "")
    # The seeded demo notes have no status.
    assert no_value.label == "No value"
    assert len(no_value.rows) > 0


def test_a_date_filter(workspace: Services) -> None:
    result = workspace.views.run(
        config(filters=[ViewFilter(field="updated", operator="after", value="2000-01-01")])
    )

    # Everything was created now, so everything is after 2000.
    assert result.total == len(workspace.notes.list_notes())


def test_available_properties_are_reported(workspace: Services) -> None:
    seed_projects(workspace)

    result = workspace.views.run(config())

    assert "status" in result.available_properties
    assert "priority" in result.available_properties
    assert "title" in result.available_properties


def test_folder_scope_narrows_the_view(workspace: Services) -> None:
    seed_projects(workspace)

    result = workspace.views.run(config(folder_scope="Projects"))

    assert result.total == 4
    assert all(row.folder_path.startswith("Projects") for row in result.rows)


@pytest.mark.security()
def test_a_view_never_surfaces_a_locked_note(workspace: Services) -> None:
    private, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )
    workspace.notes.create_note(
        layer_id=private.id,
        folder_path="",
        title="Acquisition",
        content="BLUEJAY",
        properties={"status": "secret"},
    )
    workspace.workspace.lock_layer(private.id)

    result = workspace.views.run(
        config(filters=[ViewFilter(field="status", operator="is_not_empty")])
    )

    assert all(row.title != "Acquisition" for row in result.rows)
    assert result.model_dump_json().find("BLUEJAY") == -1
    assert result.locked_layers_excluded == 1


def test_saved_views_round_trip(workspace: Services) -> None:
    view = config(name="My Projects", type="kanban", group_by="status")

    workspace.workspace.save_view(view)
    reloaded = workspace.workspace.saved_views()

    assert len(reloaded) == 1
    assert reloaded[0].name == "My Projects"

    workspace.workspace.delete_view(view.id)
    assert workspace.workspace.saved_views() == []
