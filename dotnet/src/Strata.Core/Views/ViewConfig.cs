using System.Text.Json.Serialization;

namespace Strata.Core.Views;

/// <summary>
/// Database-style views over Markdown notes — port of <c>app/domain/views.py</c>.
/// </summary>
/// <remarks>
/// The product rule that shapes this feature: <b>Markdown files remain the source
/// of truth.</b> A view is a <em>query</em>, not a table. Filtering, sorting and
/// grouping run over the notes' frontmatter every time; nothing is copied into a
/// database, and a note edited in another editor shows up in the next query.
/// </remarks>
[JsonConverter(typeof(JsonStringEnumConverter<ViewType>))]
public enum ViewType
{
    [JsonStringEnumMemberName("table")] Table,
    [JsonStringEnumMemberName("list")] List,
    [JsonStringEnumMemberName("cards")] Cards,
    [JsonStringEnumMemberName("kanban")] Kanban,
    [JsonStringEnumMemberName("calendar")] Calendar,
    [JsonStringEnumMemberName("timeline")] Timeline,
    [JsonStringEnumMemberName("gallery")] Gallery,
}

[JsonConverter(typeof(JsonStringEnumConverter<FilterOperator>))]
public enum FilterOperator
{
    [JsonStringEnumMemberName("equals")] Equals,
    [JsonStringEnumMemberName("not_equals")] NotEquals,
    [JsonStringEnumMemberName("contains")] Contains,
    [JsonStringEnumMemberName("not_contains")] NotContains,
    [JsonStringEnumMemberName("is_empty")] IsEmpty,
    [JsonStringEnumMemberName("is_not_empty")] IsNotEmpty,
    [JsonStringEnumMemberName("greater_than")] GreaterThan,
    [JsonStringEnumMemberName("less_than")] LessThan,
    [JsonStringEnumMemberName("before")] Before,
    [JsonStringEnumMemberName("after")] After,
    [JsonStringEnumMemberName("in")] In,
}

[JsonConverter(typeof(JsonStringEnumConverter<SortDirection>))]
public enum SortDirection
{
    [JsonStringEnumMemberName("asc")] Asc,
    [JsonStringEnumMemberName("desc")] Desc,
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ViewFilter
{
    /// <summary>
    /// A property key, or one of the pseudo-fields: title, tags, folder, layer,
    /// created, updated. These are computed, not stored, so a note that never wrote
    /// a <c>title:</c> frontmatter still filters on its filename-derived title.
    /// </summary>
    public required string Field { get; init; }

    public FilterOperator Operator { get; init; } = FilterOperator.Equals;

    public string Value { get; init; } = string.Empty;

    public const int MaxFieldLength = 120;
    public const int MaxValueLength = 2000;
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ViewSort
{
    public required string Field { get; init; }

    public SortDirection Direction { get; init; } = SortDirection.Asc;
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ViewConfig
{
    public required string Id { get; init; }

    public required string Name { get; init; }

    public ViewType Type { get; init; } = ViewType.Table;

    /// <summary>Empty means all readable layers.</summary>
    public IReadOnlyList<string> LayerIds { get; init; } = [];

    public string FolderScope { get; init; } = string.Empty;

    public IReadOnlyList<ViewFilter> Filters { get; init; } = [];

    public IReadOnlyList<ViewSort> Sort { get; init; } = [];

    /// <summary>A field to group by (kanban columns, list sections).</summary>
    public string GroupBy { get; init; } = string.Empty;

    /// <summary>Empty means a default set.</summary>
    public IReadOnlyList<string> VisibleProperties { get; init; } = [];

    /// <summary>For calendar/timeline: which property holds the date.</summary>
    public string DateField { get; init; } = "updated";
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ViewRow
{
    public required string ObjectId { get; init; }

    public required string LayerId { get; init; }

    public required string LayerName { get; init; }

    public bool IsPrivate { get; init; }

    public required string Title { get; init; }

    public string FolderPath { get; init; } = string.Empty;

    public IReadOnlyList<string> Tags { get; init; } = [];

    public IReadOnlyDictionary<string, string> Properties { get; init; } = new Dictionary<string, string>();

    public required string CreatedAt { get; init; }

    public required string UpdatedAt { get; init; }

    public string Snippet { get; init; } = string.Empty;
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ViewGroup
{
    /// <summary>The group value ("in progress", "2026-07", …); "" is the ungrouped bucket.</summary>
    public required string Key { get; init; }

    public required string Label { get; init; }

    public IReadOnlyList<ViewRow> Rows { get; init; } = [];
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ViewResult
{
    public required ViewConfig Config { get; init; }

    public IReadOnlyList<ViewRow> Rows { get; init; } = [];

    public IReadOnlyList<ViewGroup> Groups { get; init; } = [];

    public int Total { get; init; }

    /// <summary>Property keys present across the result, so the UI can offer columns.</summary>
    public IReadOnlyList<string> AvailableProperties { get; init; } = [];

    public int LockedLayersExcluded { get; init; }
}
