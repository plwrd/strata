using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Strata.Core.Errors;

namespace Strata.Infrastructure.Storage;

/// <summary>One knowledge object, as recorded in the encrypted manifest.</summary>
public sealed class ManifestEntry
{
    /// <summary>Hex; the file under <c>objects/&lt;xx&gt;/</c>.</summary>
    public required string ObjectId { get; set; }

    /// <summary>"note" | "folder" | "attachment".</summary>
    public string Kind { get; set; } = "note";

    public string Title { get; set; } = string.Empty;

    public string FolderPath { get; set; } = string.Empty;

    public string? ParentId { get; set; }

    public string Filename { get; set; } = string.Empty;

    public List<string> Tags { get; set; } = [];

    public List<string> Aliases { get; set; } = [];

    public JsonObject Properties { get; set; } = [];

    public string CreatedAt { get; set; } = string.Empty;

    public string UpdatedAt { get; set; } = string.Empty;

    public int SizeBytes { get; set; }

    public int WordCount { get; set; }

    public string? TrashedAt { get; set; }

    public JsonObject ToJson() => new()
    {
        ["object_id"] = ObjectId,
        ["kind"] = Kind,
        ["title"] = Title,
        ["folder_path"] = FolderPath,
        ["parent_id"] = ParentId,
        ["filename"] = Filename,
        ["tags"] = new JsonArray(Tags.Select(tag => (JsonNode)JsonValue.Create(tag)!).ToArray()),
        ["aliases"] = new JsonArray(Aliases.Select(alias => (JsonNode)JsonValue.Create(alias)!).ToArray()),
        ["properties"] = Properties.DeepClone(),
        ["created_at"] = CreatedAt,
        ["updated_at"] = UpdatedAt,
        ["size_bytes"] = SizeBytes,
        ["word_count"] = WordCount,
        ["trashed_at"] = TrashedAt,
    };

    public static ManifestEntry FromJson(JsonObject raw) => new()
    {
        ObjectId = raw["object_id"]!.GetValue<string>(),
        Kind = raw["kind"]?.GetValue<string>() ?? "note",
        Title = raw["title"]?.GetValue<string>() ?? string.Empty,
        FolderPath = raw["folder_path"]?.GetValue<string>() ?? string.Empty,
        ParentId = raw["parent_id"]?.GetValue<string>(),
        Filename = raw["filename"]?.GetValue<string>() ?? string.Empty,
        Tags = Strings(raw["tags"]),
        Aliases = Strings(raw["aliases"]),
        Properties = raw["properties"] is JsonObject properties ? properties.DeepClone().AsObject() : [],
        CreatedAt = raw["created_at"]?.GetValue<string>() ?? string.Empty,
        UpdatedAt = raw["updated_at"]?.GetValue<string>() ?? string.Empty,
        SizeBytes = raw["size_bytes"]?.GetValue<int>() ?? 0,
        WordCount = raw["word_count"]?.GetValue<int>() ?? 0,
        TrashedAt = raw["trashed_at"]?.GetValue<string>(),
    };

    private static List<string> Strings(JsonNode? node)
        => node is JsonArray array
            ? array.Where(item => item is not null).Select(item => item!.GetValue<string>()).ToList()
            : [];
}

/// <summary>
/// The encrypted index of a private layer — port of
/// <c>app/infrastructure/storage/encrypted_store.py::Manifest</c>.
/// </summary>
/// <remarks>
/// The real structure — titles, the folder tree, tags, properties, links,
/// attachment names — lives here, inside an encrypted object, not on disk as
/// filenames.
/// </remarks>
public sealed class Manifest
{
    public const int FormatVersionValue = 1;

    public int FormatVersion { get; set; } = FormatVersionValue;

    /// <summary>Keyed by object id; insertion-ordered to match the Python dict.</summary>
    public Dictionary<string, ManifestEntry> Entries { get; } = [];

    public byte[] ToBytes()
    {
        var payload = new JsonObject
        {
            ["format_version"] = FormatVersion,
            ["entries"] = new JsonArray(Entries.Values.Select(entry => (JsonNode)entry.ToJson()).ToArray()),
        };

        // Compact separators, like json.dumps(..., separators=(",", ":")).
        return Encoding.UTF8.GetBytes(payload.ToJsonString());
    }

    public static Manifest FromBytes(ReadOnlySpan<byte> raw)
    {
        JsonNode? node;
        try
        {
            node = JsonNode.Parse(Encoding.UTF8.GetString(raw));
        }
        catch (JsonException exc)
        {
            // The manifest authenticated but will not parse: that is corruption we
            // cannot paper over, and pretending the layer is empty would look like
            // data loss.
            throw new DecryptionException("The layer manifest is corrupt.", exc);
        }

        if (node is not JsonObject payload)
        {
            throw new DecryptionException("The layer manifest is corrupt.");
        }

        var version = payload["format_version"]?.GetValue<int>() ?? 0;
        if (version > FormatVersionValue)
        {
            throw new DecryptionException("This layer was written by a newer version of Strata.");
        }

        var manifest = new Manifest { FormatVersion = version };
        if (payload["entries"] is JsonArray entries)
        {
            foreach (var item in entries)
            {
                if (item is JsonObject entry)
                {
                    var parsed = ManifestEntry.FromJson(entry);
                    manifest.Entries[parsed.ObjectId] = parsed;
                }
            }
        }

        return manifest;
    }
}
