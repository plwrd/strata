using System.Globalization;
using System.Text.Json.Nodes;
using Strata.Core.Layers;
using Strata.Core.Notes;
using Strata.Core.Views;

namespace Strata.Services;

/// <summary>
/// Query notes for a structured view — port of
/// <c>app/services/view_service.py</c>.
/// </summary>
/// <remarks>
/// <para>
/// Filters, sorts and groups over the readable notes. A locked layer contributes
/// nothing — the candidate set is <see cref="INoteSource.ListNotes"/>, which is
/// readable-only, so a view can never surface a locked note's title or property by
/// construction rather than by a filter that could be forgotten.
/// </para>
/// <para>
/// Everything is computed from the live notes on each call. That is the whole point
/// of "Markdown stays the source of truth": the view is a lens, not a store.
/// </para>
/// </remarks>
public sealed class ViewService(WorkspaceService workspace, INoteSource notes)
{
    public const int SnippetLength = 140;

    /// <summary>Sentinel for the ungrouped bucket; cannot collide with a real value.</summary>
    private const string Ungrouped = "\0ungrouped";

    public ViewResult Run(ViewConfig config)
    {
        var layerIds = config.LayerIds.Count > 0 ? config.LayerIds : null;
        var candidates = notes.ListNotes(layerIds);

        var layerNames = workspace.Descriptor.Layers.ToDictionary(
            layer => layer.Id, layer => layer.DisplayName, StringComparer.Ordinal);
        var privateLayers = workspace.Descriptor.Layers
            .Where(layer => layer.Visibility == LayerVisibility.Private)
            .Select(layer => layer.Id)
            .ToHashSet(StringComparer.Ordinal);

        var rows = new List<ViewRow>();
        foreach (var note in candidates)
        {
            if (config.FolderScope.Length > 0
                && !note.Metadata.FolderPath.StartsWith(config.FolderScope, StringComparison.Ordinal))
            {
                continue;
            }

            if (!config.Filters.All(filter => Matches(note, filter)))
            {
                continue;
            }

            rows.Add(Row(note, layerNames, privateLayers));
        }

        rows = Sort(rows, config);
        var groups = Group(rows, config);

        var available = rows.SelectMany(row => row.Properties.Keys)
            .Concat(["title", "tags", "folder", "created", "updated"])
            .Distinct(StringComparer.Ordinal)
            .Order(StringComparer.Ordinal)
            .ToList();

        return new ViewResult
        {
            Config = config,
            Rows = rows,
            Groups = groups,
            Total = rows.Count,
            AvailableProperties = available,
            LockedLayersExcluded = workspace.LockedLayers().Count,
        };
    }

    // -- rows ----------------------------------------------------------------

    private static ViewRow Row(Note note, Dictionary<string, string> layerNames, HashSet<string> privateLayers)
    {
        var meta = note.Metadata;
        return new ViewRow
        {
            ObjectId = meta.Id,
            LayerId = meta.LayerId,
            LayerName = layerNames.GetValueOrDefault(meta.LayerId, "Unknown"),
            IsPrivate = privateLayers.Contains(meta.LayerId),
            Title = meta.Title,
            FolderPath = meta.FolderPath,
            Tags = [.. meta.Tags],
            Properties = meta.Properties
                .Where(pair => pair.Key is not ("tags" or "title"))
                .ToDictionary(pair => pair.Key, pair => Stringify(pair.Value), StringComparer.Ordinal),
            CreatedAt = meta.CreatedAt,
            UpdatedAt = meta.UpdatedAt,
            Snippet = Snippet(note.Content),
        };
    }

    private static string Snippet(string content)
    {
        var trimmed = content.Trim();
        var clipped = trimmed.Length > SnippetLength ? trimmed[..SnippetLength] : trimmed;
        return clipped.Replace("\n", " ", StringComparison.Ordinal);
    }

    private static string Field(ViewRow row, string field) => field switch
    {
        "title" => row.Title,
        "tags" => string.Join(", ", row.Tags),
        "folder" => row.FolderPath,
        "layer" => row.LayerName,
        "created" => row.CreatedAt,
        "updated" => row.UpdatedAt,
        _ => row.Properties.GetValueOrDefault(field, string.Empty),
    };

    /// <summary>
    /// The same pseudo-fields as <see cref="Field"/>, but resolved on the note.
    /// </summary>
    /// <remarks>
    /// Note the deliberate difference from the row version: filtering on "layer"
    /// matches the layer <em>id</em>, while a row displays the layer's name. Both
    /// behaviours are inherited from the Python original.
    /// </remarks>
    private static string NoteField(Note note, string field)
    {
        var meta = note.Metadata;
        return field switch
        {
            "title" => meta.Title,
            "tags" => string.Join(", ", meta.Tags),
            "folder" => meta.FolderPath,
            "layer" => meta.LayerId,
            "created" => meta.CreatedAt,
            "updated" => meta.UpdatedAt,
            _ => Stringify(meta.Properties.TryGetPropertyValue(field, out var value) ? value : null),
        };
    }

    // -- filtering -----------------------------------------------------------

    private static bool Matches(Note note, ViewFilter filter)
    {
        var value = NoteField(note, filter.Field);
        var target = filter.Value;

        return filter.Operator switch
        {
            FilterOperator.IsEmpty => value.Length == 0,
            FilterOperator.IsNotEmpty => value.Length > 0,
            FilterOperator.Equals => Same(value, target),
            FilterOperator.NotEquals => !Same(value, target),
            FilterOperator.Contains => value.Contains(target, StringComparison.OrdinalIgnoreCase),
            FilterOperator.NotContains => !value.Contains(target, StringComparison.OrdinalIgnoreCase),
            FilterOperator.In => target.Split(',')
                .Select(item => item.Trim())
                .Any(item => Same(value, item)),
            FilterOperator.GreaterThan or FilterOperator.LessThan
                => NumericCompare(value, target, filter.Operator),
            FilterOperator.Before or FilterOperator.After
                => DateCompare(value, target, filter.Operator),
            _ => false,
        };
    }

    private static bool Same(string left, string right)
        => string.Equals(left, right, StringComparison.OrdinalIgnoreCase);

    private static bool NumericCompare(string value, string target, FilterOperator op)
    {
        if (!TryNumber(value, out var left) || !TryNumber(target, out var right))
        {
            return false;
        }

        return op == FilterOperator.GreaterThan ? left > right : left < right;
    }

    private static bool DateCompare(string value, string target, FilterOperator op)
    {
        if (ParseDate(value) is not { } left || ParseDate(target) is not { } right)
        {
            return false;
        }

        return op == FilterOperator.Before ? left < right : left > right;
    }

    // -- sorting and grouping ------------------------------------------------

    private static List<ViewRow> Sort(List<ViewRow> rows, ViewConfig config)
    {
        if (config.Sort.Count == 0)
        {
            return rows.OrderBy(row => row.Title.ToLowerInvariant(), StringComparer.Ordinal).ToList();
        }

        // Apply each key in reverse with a stable sort, so the first key wins —
        // the same technique the Python original uses.
        foreach (var sort in config.Sort.Reverse())
        {
            rows = sort.Direction == SortDirection.Desc
                ? rows.OrderByDescending(row => SortKey(Field(row, sort.Field))).ToList()
                : rows.OrderBy(row => SortKey(Field(row, sort.Field))).ToList();
        }

        return rows;
    }

    private static List<ViewGroup> Group(List<ViewRow> rows, ViewConfig config)
    {
        if (config.GroupBy.Length == 0)
        {
            return [];
        }

        var buckets = new Dictionary<string, List<ViewRow>>(StringComparer.Ordinal);
        var order = new List<string>();

        foreach (var row in rows)
        {
            var value = Field(row, config.GroupBy);
            var key = value.Length > 0 ? value : Ungrouped;
            if (!buckets.TryGetValue(key, out var bucket))
            {
                bucket = [];
                buckets[key] = bucket;
                order.Add(key);
            }

            bucket.Add(row);
        }

        return order.Select(key => new ViewGroup
        {
            Key = key == Ungrouped ? string.Empty : key,
            Label = key == Ungrouped ? "No value" : key,
            Rows = buckets[key],
        }).ToList();
    }

    // -- coercion ------------------------------------------------------------

    private static string Stringify(JsonNode? value)
    {
        if (value is null)
        {
            return string.Empty;
        }

        if (value is JsonArray array)
        {
            return string.Join(", ", array.Select(Stringify));
        }

        if (value is JsonValue scalar)
        {
            if (scalar.TryGetValue<bool>(out var flag))
            {
                return flag ? "true" : "false";
            }

            if (scalar.TryGetValue<string>(out var text))
            {
                return text;
            }
        }

        return value.ToJsonString().Trim('"');
    }

    private static bool TryNumber(string value, out double number)
        => double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out number);

    /// <summary>Numbers sort numerically, everything else lexically, empties last.</summary>
    private static (int Bucket, double Number, string Text) SortKey(string value)
    {
        if (value.Length == 0)
        {
            return (2, 0.0, string.Empty);
        }

        return TryNumber(value, out var number)
            ? (0, number, string.Empty)
            : (1, 0.0, value.ToLowerInvariant());
    }

    private static DateTimeOffset? ParseDate(string value)
    {
        if (value.Length == 0)
        {
            return null;
        }

        if (DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var parsed))
        {
            return parsed;
        }

        // Fall back to the leading date, matching Python's date.fromisoformat(value[:10]).
        if (value.Length >= 10
            && DateOnly.TryParseExact(value[..10], "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var date))
        {
            return new DateTimeOffset(date.ToDateTime(TimeOnly.MinValue), TimeSpan.Zero);
        }

        return null;
    }
}
