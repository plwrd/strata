using System.Text.Json.Nodes;
using Strata.Core.Layers;
using Strata.Core.Notes;
using Strata.Core.Views;

namespace Strata.Services.Tests;

/// <summary>
/// The view query engine: filter, sort, group. A filter that silently drops a row
/// or a sort that reorders one wrongly is a bug the user cannot see — the note is
/// still on disk, just missing from the view.
/// </summary>
public class ViewServiceTests : IDisposable
{
    private readonly WorkspaceFixture _fixture = new();
    private readonly WorkspaceService _workspace;
    private readonly string _layerId;

    public ViewServiceTests()
    {
        _workspace = _fixture.Created();
        _layerId = _fixture.PublicLayerId;
        _workspace.RenameLayer(_layerId, "Public");
    }

    public void Dispose()
    {
        _fixture.Dispose();
        GC.SuppressFinalize(this);
    }

    /// <summary>A hand-built candidate set, so the query logic is tested on its own.</summary>
    private sealed class FakeNotes(params Note[] notes) : INoteSource
    {
        public List<string>? RequestedLayerIds { get; private set; }

        public List<Note> ListNotes(IReadOnlyList<string>? layerIds = null)
        {
            RequestedLayerIds = layerIds?.ToList();
            return [.. notes];
        }

        public List<FolderNode> ListFolders(IReadOnlyList<string>? layerIds = null) => [];
    }

    private Note Note(
        string title,
        string folder = "",
        string content = "",
        JsonObject? properties = null,
        IReadOnlyList<string>? tags = null,
        string created = "2020-01-01T00:00:00+00:00",
        string updated = "2020-01-01T00:00:00+00:00")
        => new(
            new NoteMetadata
            {
                Id = title.ToLowerInvariant(),
                LayerId = _layerId,
                Title = title,
                FolderPath = folder,
                Tags = tags ?? [],
                Properties = properties ?? [],
                CreatedAt = created,
                UpdatedAt = updated,
            },
            content);

    private ViewService Service(params Note[] notes) => new(_workspace, new FakeNotes(notes));

    private static ViewConfig Config(
        IReadOnlyList<ViewFilter>? filters = null,
        IReadOnlyList<ViewSort>? sort = null,
        string groupBy = "",
        string folderScope = "")
        => new()
        {
            Id = "v",
            Name = "View",
            Filters = filters ?? [],
            Sort = sort ?? [],
            GroupBy = groupBy,
            FolderScope = folderScope,
        };

    // -- rows ----------------------------------------------------------------

    [Fact]
    public void Rows_carry_the_layer_name_and_privacy_flag()
    {
        var result = Service(Note("A")).Run(Config());

        var row = Assert.Single(result.Rows);
        Assert.Equal("Public", row.LayerName);
        Assert.False(row.IsPrivate);
        Assert.Equal(1, result.Total);
    }

    [Fact]
    public void A_note_from_a_private_layer_is_flagged()
    {
        var (privateLayer, _) = _workspace.CreateLayer(
            "Private", LayerVisibility.Private, password: WorkspaceFixture.Password);

        var note = Note("Secret") with
        {
            Metadata = Note("Secret").Metadata with { LayerId = privateLayer.Id },
        };

        var row = Assert.Single(Service(note).Run(Config()).Rows);
        Assert.True(row.IsPrivate);
        Assert.Equal("Private", row.LayerName);
    }

    [Fact]
    public void An_unknown_layer_shows_as_unknown_rather_than_crashing()
    {
        var note = Note("Orphan") with
        {
            Metadata = Note("Orphan").Metadata with { LayerId = "layer_gone" },
        };

        Assert.Equal("Unknown", Assert.Single(Service(note).Run(Config()).Rows).LayerName);
    }

    [Fact]
    public void The_snippet_is_clipped_and_flattened()
    {
        var content = "  line one\nline two" + new string('x', 200);

        var snippet = Assert.Single(Service(Note("A", content: content)).Run(Config()).Rows).Snippet;

        Assert.Equal(ViewService.SnippetLength, snippet.Length);
        Assert.DoesNotContain('\n', snippet);
        Assert.StartsWith("line one line two", snippet, StringComparison.Ordinal);
    }

    [Fact]
    public void Tags_and_title_are_not_repeated_as_properties()
    {
        var properties = new JsonObject { ["title"] = "dup", ["tags"] = new JsonArray("x"), ["status"] = "open" };

        var row = Assert.Single(Service(Note("A", properties: properties)).Run(Config()).Rows);

        Assert.Equal(["status"], row.Properties.Keys);
    }

    [Fact]
    public void Property_values_are_stringified_the_way_python_does()
    {
        var properties = new JsonObject
        {
            ["flag"] = true,
            ["count"] = 3,
            ["ratio"] = 1.5,
            ["list"] = new JsonArray("a", "b"),
            ["text"] = "plain",
        };

        var row = Assert.Single(Service(Note("A", properties: properties)).Run(Config()).Rows);

        Assert.Equal("true", row.Properties["flag"]);
        Assert.Equal("3", row.Properties["count"]);
        Assert.Equal("1.5", row.Properties["ratio"]);
        Assert.Equal("a, b", row.Properties["list"]);
        Assert.Equal("plain", row.Properties["text"]);
    }

    [Fact]
    public void Available_properties_always_include_the_pseudo_fields()
    {
        var result = Service(Note("A", properties: new JsonObject { ["status"] = "open" })).Run(Config());

        Assert.Equal(["created", "folder", "status", "tags", "title", "updated"], result.AvailableProperties);
    }

    // -- scoping and filtering ------------------------------------------------

    [Fact]
    public void An_empty_layer_list_means_all_readable_layers()
    {
        var source = new FakeNotes(Note("A"));
        new ViewService(_workspace, source).Run(Config());

        Assert.Null(source.RequestedLayerIds);
    }

    [Fact]
    public void A_layer_list_narrows_the_candidate_set()
    {
        var source = new FakeNotes(Note("A"));
        new ViewService(_workspace, source).Run(Config() with { LayerIds = [_layerId] });

        Assert.Equal([_layerId], source.RequestedLayerIds);
    }

    [Fact]
    public void Folder_scope_keeps_only_notes_beneath_it()
    {
        var result = Service(Note("In", "Deals"), Note("Deeper", "Deals/Q1"), Note("Out", "Other"))
            .Run(Config(folderScope: "Deals"));

        Assert.Equal(["Deeper", "In"], result.Rows.Select(row => row.Title));
    }

    [Theory]
    [InlineData(FilterOperator.Equals, "open", new[] { "Open" })]
    [InlineData(FilterOperator.NotEquals, "open", new[] { "Done", "Untouched" })]
    [InlineData(FilterOperator.Contains, "pe", new[] { "Open" })]
    [InlineData(FilterOperator.NotContains, "pe", new[] { "Done", "Untouched" })]
    [InlineData(FilterOperator.IsEmpty, "", new[] { "Untouched" })]
    [InlineData(FilterOperator.IsNotEmpty, "", new[] { "Done", "Open" })]
    [InlineData(FilterOperator.In, "open, done", new[] { "Done", "Open" })]
    public void String_operators_match_case_insensitively(
        FilterOperator op, string value, string[] expected)
    {
        var notes = new[]
        {
            Note("Open", properties: new JsonObject { ["status"] = "OPEN" }),
            Note("Done", properties: new JsonObject { ["status"] = "done" }),
            Note("Untouched"),
        };

        var result = Service(notes).Run(
            Config([new ViewFilter { Field = "status", Operator = op, Value = value }]));

        Assert.Equal(expected, result.Rows.Select(row => row.Title));
    }

    [Theory]
    [InlineData(FilterOperator.GreaterThan, "2", new[] { "High" })]
    [InlineData(FilterOperator.LessThan, "2", new[] { "Low" })]
    public void Numeric_operators_compare_numerically(FilterOperator op, string value, string[] expected)
    {
        var notes = new[]
        {
            Note("Low", properties: new JsonObject { ["priority"] = 1 }),
            Note("High", properties: new JsonObject { ["priority"] = 10 }),
            Note("Words", properties: new JsonObject { ["priority"] = "urgent" }),
        };

        var result = Service(notes).Run(
            Config([new ViewFilter { Field = "priority", Operator = op, Value = value }]));

        Assert.Equal(expected, result.Rows.Select(row => row.Title));
    }

    [Fact]
    public void A_non_numeric_value_never_matches_a_numeric_operator()
    {
        var note = Note("Words", properties: new JsonObject { ["priority"] = "urgent" });

        var result = Service(note).Run(Config(
            [new ViewFilter { Field = "priority", Operator = FilterOperator.GreaterThan, Value = "0" }]));

        Assert.Empty(result.Rows);
    }

    [Theory]
    [InlineData(FilterOperator.Before, new[] { "Old" })]
    [InlineData(FilterOperator.After, new[] { "New" })]
    public void Date_operators_compare_chronologically(FilterOperator op, string[] expected)
    {
        var notes = new[]
        {
            Note("Old", updated: "2019-06-01T00:00:00+00:00"),
            Note("New", updated: "2021-06-01T00:00:00+00:00"),
        };

        var result = Service(notes).Run(
            Config([new ViewFilter { Field = "updated", Operator = op, Value = "2020-01-01" }]));

        Assert.Equal(expected, result.Rows.Select(row => row.Title));
    }

    [Fact]
    public void An_unparseable_date_never_matches()
    {
        var note = Note("Broken", properties: new JsonObject { ["due"] = "not a date" });

        var result = Service(note).Run(Config(
            [new ViewFilter { Field = "due", Operator = FilterOperator.Before, Value = "2020-01-01" }]));

        Assert.Empty(result.Rows);
    }

    [Fact]
    public void Filters_are_combined_with_and()
    {
        var notes = new[]
        {
            Note("Both", properties: new JsonObject { ["status"] = "open", ["priority"] = 5 }),
            Note("One", properties: new JsonObject { ["status"] = "open", ["priority"] = 1 }),
        };

        var result = Service(notes).Run(Config(
        [
            new ViewFilter { Field = "status", Operator = FilterOperator.Equals, Value = "open" },
            new ViewFilter { Field = "priority", Operator = FilterOperator.GreaterThan, Value = "3" },
        ]));

        Assert.Equal(["Both"], result.Rows.Select(row => row.Title));
    }

    [Fact]
    public void Filtering_on_layer_matches_the_id_while_a_row_shows_the_name()
    {
        var result = Service(Note("A")).Run(Config(
            [new ViewFilter { Field = "layer", Operator = FilterOperator.Equals, Value = _layerId }]));

        Assert.Equal("Public", Assert.Single(result.Rows).LayerName);
    }

    [Fact]
    public void Filtering_on_tags_matches_the_joined_list()
    {
        var result = Service(Note("A", tags: ["deals", "q1"])).Run(Config(
            [new ViewFilter { Field = "tags", Operator = FilterOperator.Contains, Value = "q1" }]));

        Assert.Single(result.Rows);
    }

    // -- sorting -------------------------------------------------------------

    [Fact]
    public void Without_a_sort_rows_come_back_by_title()
    {
        var result = Service(Note("charlie"), Note("Alpha"), Note("bravo")).Run(Config());

        Assert.Equal(["Alpha", "bravo", "charlie"], result.Rows.Select(row => row.Title));
    }

    [Fact]
    public void Numbers_sort_numerically_and_empties_go_last()
    {
        var notes = new[]
        {
            Note("Ten", properties: new JsonObject { ["priority"] = 10 }),
            Note("Two", properties: new JsonObject { ["priority"] = 2 }),
            Note("None"),
            Note("Words", properties: new JsonObject { ["priority"] = "urgent" }),
        };

        var result = Service(notes).Run(
            Config(sort: [new ViewSort { Field = "priority" }]));

        Assert.Equal(["Two", "Ten", "Words", "None"], result.Rows.Select(row => row.Title));
    }

    [Fact]
    public void Descending_reverses_the_order()
    {
        var result = Service(Note("A"), Note("B")).Run(
            Config(sort: [new ViewSort { Field = "title", Direction = SortDirection.Desc }]));

        Assert.Equal(["B", "A"], result.Rows.Select(row => row.Title));
    }

    [Fact]
    public void The_first_sort_key_wins_and_later_keys_break_ties()
    {
        var notes = new[]
        {
            Note("B", properties: new JsonObject { ["group"] = "x" }),
            Note("A", properties: new JsonObject { ["group"] = "x" }),
            Note("C", properties: new JsonObject { ["group"] = "a" }),
        };

        var result = Service(notes).Run(Config(sort:
        [
            new ViewSort { Field = "group" },
            new ViewSort { Field = "title" },
        ]));

        Assert.Equal(["C", "A", "B"], result.Rows.Select(row => row.Title));
    }

    // -- grouping ------------------------------------------------------------

    [Fact]
    public void Without_a_group_by_there_are_no_groups()
        => Assert.Empty(Service(Note("A")).Run(Config()).Groups);

    [Fact]
    public void Groups_follow_the_order_rows_first_appear_in()
    {
        var notes = new[]
        {
            Note("B", properties: new JsonObject { ["status"] = "doing" }),
            Note("A", properties: new JsonObject { ["status"] = "todo" }),
            Note("C", properties: new JsonObject { ["status"] = "doing" }),
        };

        // Sorted by title first: A(todo), B(doing), C(doing).
        var groups = Service(notes).Run(Config(groupBy: "status")).Groups;

        Assert.Equal(["todo", "doing"], groups.Select(group => group.Key));
        Assert.Equal(["B", "C"], groups[1].Rows.Select(row => row.Title));
    }

    [Fact]
    public void Rows_with_no_value_land_in_a_labelled_ungrouped_bucket()
    {
        var groups = Service(Note("A"), Note("B", properties: new JsonObject { ["status"] = "todo" }))
            .Run(Config(groupBy: "status")).Groups;

        var ungrouped = groups.Single(group => group.Key.Length == 0);
        Assert.Equal("No value", ungrouped.Label);
        Assert.Equal(["A"], ungrouped.Rows.Select(row => row.Title));
    }

    // -- locked layers --------------------------------------------------------

    [Fact]
    public void Locked_layers_are_counted_but_contribute_nothing()
    {
        var (privateLayer, _) = _workspace.CreateLayer(
            "Private", LayerVisibility.Private, password: WorkspaceFixture.Password);
        _workspace.LockLayer(privateLayer.Id);

        var result = Service(Note("Visible")).Run(Config());

        Assert.Equal(1, result.LockedLayersExcluded);
        Assert.Equal(["Visible"], result.Rows.Select(row => row.Title));
    }
}
