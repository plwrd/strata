using Strata.Core.Errors;
using Strata.Core.Json;
using Strata.Core.Layers;
using Strata.Core.Schema;
using Strata.Core.Views;
using Strata.Core.Workspaces;
using Strata.Infrastructure.Storage;

namespace Strata.Services.Tests;

/// <summary>Workspace and layer lifecycle.</summary>
public class WorkspaceServiceTests : IDisposable
{
    private readonly WorkspaceFixture _fixture = new();

    public void Dispose()
    {
        _fixture.Dispose();
        GC.SuppressFinalize(this);
    }

    // -- lifecycle -----------------------------------------------------------

    [Fact]
    public void Creating_a_workspace_lays_out_the_default_knowledge_areas()
    {
        var workspace = _fixture.Created("My Knowledge");

        Assert.True(workspace.IsOpen);
        Assert.Equal("My Knowledge", workspace.Descriptor.Name);
        Assert.StartsWith("ws_", workspace.Descriptor.Id, StringComparison.Ordinal);

        var layer = Assert.Single(workspace.Descriptor.Layers);
        Assert.Equal("Knowledge", layer.DisplayName);
        Assert.Equal(LayerStorage.Markdown, layer.Storage);

        var layerRoot = workspace.LayerStore(layer.Id).Root;
        foreach (var area in KnowledgeAreas.All)
        {
            Assert.True(Directory.Exists(Path.Combine(layerRoot, area)), $"missing area {area}");
        }
    }

    [Fact]
    public void Creating_over_an_existing_workspace_is_refused()
    {
        _fixture.Created();

        Assert.Throws<InvalidRequestException>(() => new WorkspaceService().Create(_fixture.Root, "Again"));
    }

    [Fact]
    public void Creating_installs_the_three_default_lenses()
    {
        var lenses = _fixture.Created().Lenses();

        Assert.Equal(["lens_all", "lens_recent", "lens_public"], lenses.Select(lens => lens.Id));
        Assert.True(lenses[0].IsDefault);
        Assert.Equal(14, lenses[1].TimeRangeDays);
    }

    [Fact]
    public void The_open_callback_fires_with_the_root()
    {
        _fixture.Created();

        Assert.Equal([_fixture.Root], _fixture.Opened);
    }

    [Fact]
    public void Nothing_is_readable_before_a_workspace_is_open()
    {
        var workspace = new WorkspaceService();

        Assert.False(workspace.IsOpen);
        Assert.Throws<NotFoundException>(() => workspace.Descriptor);
        Assert.Throws<NotFoundException>(() => workspace.Root);
    }

    [Fact]
    public void Open_reads_back_what_create_wrote()
    {
        _fixture.Created("Persisted");
        var layerId = _fixture.PublicLayerId;

        var reopened = new WorkspaceService().Open(_fixture.Root);

        Assert.Equal("Persisted", reopened.Name);
        Assert.Equal(layerId, Assert.Single(reopened.Layers).Id);
    }

    [Fact]
    public void OpenOrCreate_creates_once_then_opens()
    {
        var first = new WorkspaceService().OpenOrCreate(_fixture.Root, "Once");
        var second = new WorkspaceService().OpenOrCreate(_fixture.Root, "Ignored");

        Assert.Equal(first.Id, second.Id);
        Assert.Equal("Once", second.Name);
    }

    [Fact]
    public void Close_locks_everything_and_forgets_the_workspace()
    {
        var workspace = _fixture.Created();

        workspace.Close();

        Assert.False(workspace.IsOpen);
        Assert.Equal(1, _fixture.Closed);
        Assert.Throws<NotFoundException>(() => workspace.Descriptor);
    }

    // -- layers --------------------------------------------------------------

    [Fact]
    public void A_new_public_layer_is_mounted_and_appended_to_the_order()
    {
        var workspace = _fixture.Created();

        var (layer, recovery) = workspace.CreateLayer("Second");

        Assert.Null(recovery);
        Assert.Equal(LayerState.Mounted, layer.State);
        Assert.Equal(LayerVisibility.Public, layer.Visibility);
        Assert.Equal("layer-public", layer.Color);
        Assert.Equal(layer.Id, workspace.Descriptor.LayerOrder[^1]);
        Assert.StartsWith("layer_", layer.Id, StringComparison.Ordinal);
    }

    [Fact]
    public void A_private_layer_without_a_password_is_refused()
        => Assert.Throws<InvalidRequestException>(
            () => _fixture.Created().CreateLayer("Private", LayerVisibility.Private));

    [Fact]
    public void A_private_layer_without_an_encryption_service_is_unsupported()
    {
        var workspace = new WorkspaceService();
        workspace.Create(_fixture.Root, "No crypto");

        Assert.Throws<UnsupportedException>(() => workspace.CreateLayer(
            "Private", LayerVisibility.Private, password: WorkspaceFixture.Password));
    }

    [Fact]
    public void Renaming_a_layer_persists()
    {
        var workspace = _fixture.Created();
        var layerId = _fixture.PublicLayerId;

        workspace.RenameLayer(layerId, "Renamed");

        Assert.Equal("Renamed", new WorkspaceService().Open(_fixture.Root).Layer(layerId)!.DisplayName);
    }

    [Fact]
    public void Changing_an_ai_policy_persists()
    {
        var workspace = _fixture.Created();
        var layerId = _fixture.PublicLayerId;

        workspace.SetLayerAiPolicy(layerId, new LayerAIPolicy
        {
            Access = AIAccess.Disabled,
            Embeddings = EmbeddingAccess.Disabled,
            MayRead = false,
        });

        var reloaded = new WorkspaceService().Open(_fixture.Root).Layer(layerId)!;
        Assert.Equal(AIAccess.Disabled, reloaded.AiPolicy.Access);
        Assert.False(reloaded.AiPolicy.MayRead);
        Assert.False(reloaded.AiPolicy.AllowsRemote);
    }

    [Fact]
    public void Reordering_requires_every_layer_exactly_once()
    {
        var workspace = _fixture.Created();
        var first = _fixture.PublicLayerId;
        var (second, _) = workspace.CreateLayer("Second");

        workspace.ReorderLayers([second.Id, first]);
        Assert.Equal([second.Id, first], workspace.Descriptor.OrderedLayers().Select(layer => layer.Id));

        Assert.Throws<InvalidRequestException>(() => workspace.ReorderLayers([first]));
        Assert.Throws<InvalidRequestException>(() => workspace.ReorderLayers([first, first]));
        Assert.Throws<InvalidRequestException>(() => workspace.ReorderLayers([first, second.Id, "ghost"]));
    }

    [Fact]
    public void An_unknown_layer_is_not_found()
        => Assert.Throws<NotFoundException>(() => _fixture.Created().RequireLayer("layer_ghost"));

    [Fact]
    public void A_markdown_store_is_refused_for_a_non_markdown_layer()
    {
        var workspace = _fixture.Created();
        var (layer, _) = workspace.CreateLayer(
            "Private", LayerVisibility.Private, password: WorkspaceFixture.Password);

        Assert.Throws<InvalidRequestException>(() => workspace.LayerStore(layer.Id));
    }

    [Fact]
    public void Layer_stores_are_cached_per_layer()
    {
        var workspace = _fixture.Created();
        var layerId = _fixture.PublicLayerId;

        Assert.Same(workspace.LayerStore(layerId), workspace.LayerStore(layerId));
    }

    // -- lenses and views ----------------------------------------------------

    [Fact]
    public void Saving_a_lens_replaces_by_id_rather_than_duplicating()
    {
        var workspace = _fixture.Created();
        var before = workspace.Lenses().Count;

        workspace.SaveLens(new KnowledgeLens { Id = "lens_all", Name = "Renamed" });
        workspace.SaveLens(new KnowledgeLens { Id = "lens_new", Name = "New" });

        var lenses = workspace.Lenses();
        Assert.Equal(before + 1, lenses.Count);
        Assert.Equal("Renamed", lenses.Single(lens => lens.Id == "lens_all").Name);
        Assert.Equal(0, lenses.FindIndex(lens => lens.Id == "lens_all")); // order held
    }

    [Fact]
    public void Views_round_trip_through_the_descriptor()
    {
        var workspace = _fixture.Created();
        var view = new ViewConfig { Id = "view-1", Name = "Board", Type = ViewType.Kanban, GroupBy = "status" };

        workspace.SaveView(view);
        Assert.True(StrataJson.ValueEquals(
            view, Assert.Single(new WorkspaceService().Open(_fixture.Root).SavedViews)));

        workspace.SaveView(view with { Name = "Renamed" });
        Assert.Equal("Renamed", Assert.Single(workspace.SavedViews()).Name);

        workspace.DeleteView("view-1");
        Assert.Empty(workspace.SavedViews());
    }

    [Fact]
    public void Deleting_an_unknown_view_is_a_no_op()
    {
        var workspace = _fixture.Created();
        workspace.SaveView(new ViewConfig { Id = "keep", Name = "Keep" });

        workspace.DeleteView("ghost");

        Assert.Single(workspace.SavedViews());
    }

    // -- saving --------------------------------------------------------------

    [Fact]
    public void Every_mutation_refreshes_updated_at()
    {
        var workspace = _fixture.Created();
        var before = workspace.Descriptor.UpdatedAt;

        workspace.RenameLayer(_fixture.PublicLayerId, "Touched");

        var descriptor = new WorkspaceStore(_fixture.Root).Load();
        Assert.Equal(workspace.Descriptor.UpdatedAt, descriptor.UpdatedAt);
        Assert.True(string.CompareOrdinal(descriptor.UpdatedAt, before) >= 0);
    }
}
