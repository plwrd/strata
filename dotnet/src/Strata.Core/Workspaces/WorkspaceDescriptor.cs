using System.Text.Json.Serialization;
using Strata.Core.Layers;
using Strata.Core.Views;

namespace Strata.Core.Workspaces;

/// <summary>
/// A saved perspective over one or more layers — port of
/// <c>app/domain/workspace.py::KnowledgeLens</c>.
/// </summary>
/// <remarks>
/// A lens never grants access: hiding a layer in a lens is a <em>view</em>
/// decision, unlocking one is a <em>security</em> decision.
/// <see cref="AiReadableLayerIds"/> narrows what the AI Context Composer may
/// include, but the per-layer AI policy still wins.
/// </remarks>
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record KnowledgeLens
{
    public required string Id { get; init; }

    public required string Name { get; init; }

    public IReadOnlyList<string> VisibleLayerIds { get; init; } = [];

    public IReadOnlyList<string> LayerOrder { get; init; } = [];

    public IReadOnlyList<string> FolderScope { get; init; } = [];

    public IReadOnlyList<string> TagFilters { get; init; } = [];

    public IReadOnlyDictionary<string, string> PropertyFilters { get; init; } = new Dictionary<string, string>();

    public IReadOnlyList<string> RelationshipFilters { get; init; } = [];

    public IReadOnlyList<string> NodeTypes { get; init; } = [];

    public string SearchQuery { get; init; } = string.Empty;

    public int? TimeRangeDays { get; init; }

    public string GraphLayout { get; init; } = "force";

    public IReadOnlyDictionary<string, double> GraphCamera { get; init; } = new Dictionary<string, double>();

    public string ColorMapping { get; init; } = "layer";

    public IReadOnlyList<string> AiReadableLayerIds { get; init; } = [];

    public string Mode { get; init; } = "explore";

    public bool IsDefault { get; init; }
}

/// <summary>Persisted as <c>workspace.json</c> at the workspace root.</summary>
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record WorkspaceDescriptor
{
    public const int WorkspaceFormatVersion = 1;

    public int FormatVersion { get; init; } = WorkspaceFormatVersion;

    public required string Id { get; init; }

    public required string Name { get; init; }

    public required string CreatedAt { get; init; }

    public required string UpdatedAt { get; init; }

    public IReadOnlyList<string> LayerOrder { get; init; } = [];

    public IReadOnlyList<LayerDescriptor> Layers { get; init; } = [];

    public IReadOnlyList<KnowledgeLens> Lenses { get; init; } = [];

    public IReadOnlyList<ViewConfig> SavedViews { get; init; } = [];

    public LayerDescriptor? Layer(string layerId)
        => Layers.FirstOrDefault(layer => layer.Id == layerId);

    /// <summary>
    /// Layers in <see cref="LayerOrder"/>, with anything unlisted kept at the end in
    /// its original relative order.
    /// </summary>
    public IReadOnlyList<LayerDescriptor> OrderedLayers()
    {
        var index = LayerOrder
            .Select((layerId, position) => (layerId, position))
            .ToDictionary(pair => pair.layerId, pair => pair.position);

        // OrderBy is stable, matching Python's sorted().
        return Layers.OrderBy(layer => index.GetValueOrDefault(layer.Id, index.Count)).ToList();
    }
}
