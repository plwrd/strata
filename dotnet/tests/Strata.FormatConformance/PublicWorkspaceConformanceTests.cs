using System.Text.Json;
using Strata.Core.Errors;
using Strata.Core.Layers;
using Strata.Core.Views;
using Strata.Infrastructure.Storage;

namespace Strata.FormatConformance;

/// <summary>
/// Read a public Markdown layer and a <c>workspace.json</c> that the <em>Python</em>
/// app wrote: derived note ids, frontmatter, and the descriptor schema.
/// </summary>
public class PublicWorkspaceConformanceTests
{
    private static readonly JsonElement Vector = Fixtures.Json("public_workspace.json");

    private static string WorkspaceRoot =>
        Path.Combine(Fixtures.Root, Vector.GetProperty("directory").GetString()!);

    private static string PublicLayerId => Vector.GetProperty("public_layer_id").GetString()!;

    private static WorkspaceStore Store() => new(WorkspaceRoot);

    private static MarkdownLayerStore Markdown()
        => new(PublicLayerId, Store().LayerRoot(PublicLayerId));

    private static JsonElement NoteVector(int index)
        => Vector.GetProperty("notes")[index];

    // -- workspace descriptor ------------------------------------------------

    [Fact]
    public void The_python_descriptor_loads()
    {
        var descriptor = Store().Load();

        Assert.Equal(Vector.GetProperty("workspace_id").GetString(), descriptor.Id);
        Assert.Equal("KAT Workspace", descriptor.Name);
        Assert.Equal(1, descriptor.FormatVersion);
        Assert.Equal(2, descriptor.Layers.Count);
    }

    [Fact]
    public void Layer_enums_round_trip_through_their_exact_wire_strings()
    {
        var descriptor = Store().Load();
        var priv = descriptor.Layer(Vector.GetProperty("private_layer_id").GetString()!)!;

        Assert.Equal(LayerVisibility.Private, priv.Visibility);
        Assert.Equal(LayerState.Locked, priv.State);
        Assert.Equal(LayerStorage.EncryptedObjects, priv.Storage);
        Assert.Equal(LayerSharingMode.SharedPassword, priv.SharingMode);
        Assert.Equal(AIAccess.RemoteWithConfirmation, priv.AiPolicy.Access);
        Assert.Equal(EmbeddingAccess.Disabled, priv.AiPolicy.Embeddings);
        Assert.True(priv.AiPolicy.MayApplyApprovedEdits);
    }

    [Fact]
    public void Lock_state_drives_readability_the_same_way_python_computes_it()
    {
        var descriptor = Store().Load();
        var pub = descriptor.Layer(PublicLayerId)!;
        var priv = descriptor.Layer(Vector.GetProperty("private_layer_id").GetString()!)!;

        Assert.True(pub.IsReadable);
        Assert.False(pub.IsLocked);
        Assert.False(priv.IsReadable);
        Assert.True(priv.IsLocked);
    }

    [Fact]
    public void Ordered_layers_follow_layer_order()
        => Assert.Equal(
            Vector.GetProperty("ordered_layer_ids").EnumerateArray().Select(id => id.GetString()!).ToList(),
            Store().Load().OrderedLayers().Select(layer => layer.Id).ToList());

    [Fact]
    public void Lenses_and_saved_views_survive_the_round_trip()
    {
        var descriptor = Store().Load();

        var lens = Assert.Single(descriptor.Lenses);
        Assert.Equal("Deals only", lens.Name);
        Assert.Equal(["deals"], lens.TagFilters);
        Assert.Equal("open", lens.PropertyFilters["status"]);
        Assert.Equal(0.75, lens.GraphCamera["zoom"]);
        Assert.Equal(30, lens.TimeRangeDays);
        Assert.True(lens.IsDefault);

        var view = Assert.Single(descriptor.SavedViews);
        Assert.Equal(ViewType.Kanban, view.Type);
        Assert.Equal(FilterOperator.NotEquals, Assert.Single(view.Filters).Operator);
        Assert.Equal(SortDirection.Desc, Assert.Single(view.Sort).Direction);
        Assert.Equal("status", view.GroupBy);
    }

    [Fact]
    public void Saving_and_reloading_preserves_every_field()
    {
        var original = Store().Load();
        var scratch = Path.Combine(Path.GetTempPath(), "strata-ws-" + Guid.NewGuid().ToString("N"));
        try
        {
            var store = new WorkspaceStore(scratch);
            store.Initialise(original);

            // These records hold collections, so `==` is reference equality on them —
            // unlike the Pydantic originals, which compare field values. ValueEquals
            // is the codebase's answer; see its remarks.
            Assert.True(Strata.Core.Json.StrataJson.ValueEquals(original, store.Load()));
            Assert.True(Directory.Exists(Path.Combine(scratch, WorkspaceStore.LayersDir)));
            Assert.True(Directory.Exists(Path.Combine(scratch, WorkspaceStore.InternalDir, "snapshots")));
        }
        finally
        {
            if (Directory.Exists(scratch))
            {
                Directory.Delete(scratch, recursive: true);
            }
        }
    }

    [Fact]
    public void A_descriptor_from_a_newer_format_version_is_refused_with_details()
    {
        var scratch = Path.Combine(Path.GetTempPath(), "strata-ws-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(scratch);
        try
        {
            var raw = File.ReadAllText(Path.Combine(WorkspaceRoot, WorkspaceStore.WorkspaceFile))
                .Replace("\"format_version\": 1", "\"format_version\": 99", StringComparison.Ordinal);
            File.WriteAllText(Path.Combine(scratch, WorkspaceStore.WorkspaceFile), raw);

            var error = Assert.Throws<InvalidRequestException>(() => new WorkspaceStore(scratch).Load());
            Assert.Equal(99, error.Details["found"]);
            Assert.Equal(1, error.Details["supported"]);
        }
        finally
        {
            Directory.Delete(scratch, recursive: true);
        }
    }

    [Fact]
    public void An_unknown_key_is_rejected_rather_than_silently_dropped()
    {
        var scratch = Path.Combine(Path.GetTempPath(), "strata-ws-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(scratch);
        try
        {
            var raw = File.ReadAllText(Path.Combine(WorkspaceRoot, WorkspaceStore.WorkspaceFile))
                .Replace("\"id\": \"ws-kat-0001\"", "\"id\": \"ws-kat-0001\", \"surprise\": 1", StringComparison.Ordinal);
            File.WriteAllText(Path.Combine(scratch, WorkspaceStore.WorkspaceFile), raw);

            Assert.Throws<InvalidRequestException>(() => new WorkspaceStore(scratch).Load());
        }
        finally
        {
            Directory.Delete(scratch, recursive: true);
        }
    }

    [Fact]
    public void Loading_from_an_empty_directory_is_not_found()
        => Assert.Throws<NotFoundException>(() => new WorkspaceStore(Path.GetTempPath()).Load());

    [Fact]
    public void Layer_roots_are_resolved_through_the_traversal_guard()
        => Assert.Throws<InvalidRequestException>(() => Store().LayerRoot("../escape"));

    // -- markdown layer ------------------------------------------------------

    [Fact]
    public void Note_ids_are_derived_the_same_way_as_python()
    {
        var recipe = Vector.GetProperty("note_id_recipe");

        Assert.Equal(
            recipe.GetProperty("expected").GetString(),
            MarkdownLayerStore.NoteIdFor(
                recipe.GetProperty("layer_id").GetString()!,
                recipe.GetProperty("relative_path").GetString()!));
    }

    [Fact]
    public void Both_notes_are_listed_in_path_order()
    {
        var notes = Markdown().ListNotes();

        Assert.Equal(2, notes.Count);
        Assert.Equal(
            Vector.GetProperty("notes").EnumerateArray().Select(n => n.GetProperty("id").GetString()!).Order().ToList(),
            notes.Select(note => note.Metadata.Id).Order().ToList());
    }

    [Theory]
    [InlineData(0)]
    [InlineData(1)]
    public void Each_note_parses_to_what_python_recorded(int index)
    {
        var expected = NoteVector(index);
        var located = Markdown().Locate(expected.GetProperty("id").GetString()!);

        Assert.NotNull(located);
        var note = located!.Value.Note;

        Assert.Equal(expected.GetProperty("content").GetString(), note.Content);
        Assert.Equal(expected.GetProperty("title").GetString(), note.Metadata.Title);
        Assert.Equal(expected.GetProperty("folder_path").GetString(), note.Metadata.FolderPath);
        Assert.Equal(expected.GetProperty("word_count").GetInt32(), note.Metadata.WordCount);
        Assert.Equal(
            expected.GetProperty("tags").EnumerateArray().Select(tag => tag.GetString()!).ToList(),
            note.Metadata.Tags);
        Assert.Equal(
            expected.GetProperty("link_targets").EnumerateArray().Select(t => t.GetString()!).ToList(),
            note.Metadata.Links.Select(link => link.TargetTitle).ToList());
    }

    [Fact]
    public void Frontmatter_scalars_keep_their_yaml_types()
    {
        var expected = NoteVector(0).GetProperty("properties");
        var note = Markdown().Locate(NoteVector(0).GetProperty("id").GetString()!)!.Value.Note;

        Assert.Equal(expected.GetProperty("status").GetString(), note.Metadata.Properties["status"]!.GetValue<string>());
        Assert.Equal(expected.GetProperty("priority").GetInt64(), note.Metadata.Properties["priority"]!.GetValue<long>());
        Assert.Equal(expected.GetProperty("draft").GetBoolean(), note.Metadata.Properties["draft"]!.GetValue<bool>());

        // tags and title are lifted onto the metadata, not left in properties.
        Assert.False(note.Metadata.Properties.ContainsKey("tags"));
        Assert.False(note.Metadata.Properties.ContainsKey("title"));
    }

    [Fact]
    public void Folders_are_listed_with_python_ids()
    {
        var expected = Vector.GetProperty("folders").EnumerateArray()
            .Select(folder => (folder.GetProperty("id").GetString()!, folder.GetProperty("path").GetString()!))
            .ToList();

        Assert.Equal(expected, Markdown().ListFolders().Select(folder => (folder.Id, folder.Path)).ToList());
    }

    [Fact]
    public void An_unknown_note_id_locates_nothing()
        => Assert.Null(Markdown().Locate(new string('0', 32)));
}
