using System.Text.Json;
using System.Text.Json.Nodes;
using Strata.Core.Errors;
using Strata.Core.Json;
using Strata.Core.Workspaces;

namespace Strata.Infrastructure.Storage;

/// <summary>
/// Workspace persistence — port of
/// <c>app/infrastructure/storage/workspace_store.py</c>.
/// </summary>
/// <remarks>
/// <code>
/// MyWorkspace/
///   workspace.json            # descriptor: layers, order, lenses
///   layers/
///     layer_ab12cd34/         # public layer: plain Markdown, source of truth
///       Architecture/Encryption Architecture.md
///     layer_ef56ab78/         # private layer: opaque objects only
///       layer.header
///       objects/02/02f8a7...
///   .strata/
///     logs/ trash/ snapshots/ exports/
/// </code>
/// Reads and writes the descriptor. Knows nothing about content.
/// </remarks>
public sealed class WorkspaceStore(string root)
{
    public const string WorkspaceFile = "workspace.json";
    public const string LayersDir = "layers";
    public const string InternalDir = ".strata";

    private static readonly string[] InternalSubdirectories = ["logs", "trash", "snapshots", "exports"];

    public string Root { get; } = root;

    public string DescriptorPath => Path.Combine(Root, WorkspaceFile);

    public string LayersRoot => Path.Combine(Root, LayersDir);

    public string InternalRoot => Path.Combine(Root, InternalDir);

    public bool Exists() => File.Exists(DescriptorPath);

    public string LayerRoot(string layerId) => Paths.ResolveWithin(LayersRoot, layerId);

    public void Initialise(WorkspaceDescriptor descriptor)
    {
        Directory.CreateDirectory(Root);
        Directory.CreateDirectory(LayersRoot);
        foreach (var sub in InternalSubdirectories)
        {
            Directory.CreateDirectory(Path.Combine(InternalRoot, sub));
        }

        Save(descriptor);
    }

    public WorkspaceDescriptor Load()
    {
        if (!Exists())
        {
            throw new NotFoundException("No workspace was found at this location.");
        }

        JsonNode? node;
        try
        {
            node = JsonNode.Parse(File.ReadAllText(DescriptorPath));
        }
        catch (Exception exc) when (exc is JsonException or IOException or UnauthorizedAccessException)
        {
            throw new InvalidRequestException("The workspace file could not be read.", innerException: exc);
        }

        if (node is not JsonObject raw)
        {
            throw new InvalidRequestException("The workspace file is not valid.");
        }

        var version = raw["format_version"]?.GetValue<int>() ?? 0;
        if (version > WorkspaceDescriptor.WorkspaceFormatVersion)
        {
            throw new InvalidRequestException(
                "This workspace was created by a newer version of Strata.",
                new Dictionary<string, object?>
                {
                    ["found"] = version,
                    ["supported"] = WorkspaceDescriptor.WorkspaceFormatVersion,
                });
        }

        try
        {
            return raw.Deserialize<WorkspaceDescriptor>(StrataJson.Options)
                ?? throw new InvalidRequestException("The workspace file is not valid.");
        }
        catch (Exception exc) when (exc is JsonException or NotSupportedException)
        {
            throw new InvalidRequestException("The workspace file is not valid.", innerException: exc);
        }
    }

    /// <summary>Atomic write: a crash mid-save must never destroy the descriptor.</summary>
    public void Save(WorkspaceDescriptor descriptor)
    {
        Directory.CreateDirectory(Root);
        AtomicFile.WriteText(
            DescriptorPath,
            JsonSerializer.Serialize(descriptor, StrataJson.Indented));
    }
}
