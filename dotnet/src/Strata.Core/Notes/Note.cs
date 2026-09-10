namespace Strata.Core.Notes;

/// <summary>
/// Note and folder domain models — port of <c>app/domain/note.py</c>.
/// </summary>
/// <remarks>
/// Markdown files are the source of truth. These models are the <em>parsed</em>
/// view of a note; the file on disk always wins. Nothing here is a database record.
/// </remarks>
public static class Relationships
{
    /// <summary>Typed relationships. <c>relates_to</c> is the untyped default.</summary>
    public static readonly IReadOnlySet<string> Types = new HashSet<string>
    {
        "references",
        "supports",
        "contradicts",
        "expands",
        "depends_on",
        "created_by",
        "assigned_to",
        "relates_to",
        "supersedes",
        "blocks",
        "evidence_for",
        "derived_from",
    };

    public const string Default = "references";
}

public sealed record NoteLink(string TargetTitle, string? Alias = null, string Relationship = Relationships.Default);

/// <summary>Everything about a note except its body.</summary>
public sealed record NoteMetadata
{
    public required string Id { get; init; }

    public required string LayerId { get; init; }

    public string? ParentId { get; init; }

    public required string Title { get; init; }

    public string FolderPath { get; init; } = string.Empty;

    public IReadOnlyList<string> Aliases { get; init; } = [];

    public IReadOnlyList<string> Tags { get; init; } = [];

    /// <summary>Frontmatter properties, kept as JSON so arbitrary values round-trip.</summary>
    public System.Text.Json.Nodes.JsonObject Properties { get; init; } = [];

    public IReadOnlyList<NoteLink> Links { get; init; } = [];

    public string CreatedAt { get; init; } = string.Empty;

    public string UpdatedAt { get; init; } = string.Empty;

    public int SizeBytes { get; init; }

    public int WordCount { get; init; }

    public string DisplayPath => string.IsNullOrEmpty(FolderPath) ? $"{Title}.md" : $"{FolderPath}/{Title}.md";
}

/// <summary>A note plus its Markdown body.</summary>
public sealed record Note(NoteMetadata Metadata, string Content = "");

public sealed record FolderNode(string Id, string LayerId, string Name, string Path, string? ParentId = null);
