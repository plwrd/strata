using System.Text.Json;
using System.Text.Json.Serialization;

namespace Strata.Core.Json;

/// <summary>
/// One place that decides how Strata's persisted models map to JSON.
/// </summary>
/// <remarks>
/// The Python models are Pydantic with snake_case fields and
/// <c>ConfigDict(extra="forbid")</c>. Both halves matter: the naming policy keeps
/// files readable by either app, and the individual models carry
/// <see cref="JsonUnmappedMemberHandlingAttribute"/> so an unknown key is a
/// rejection rather than a silent drop — a descriptor written by a newer Strata
/// must fail loudly, not lose fields on the next save.
/// </remarks>
public static class StrataJson
{
    public static JsonSerializerOptions Options { get; } = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
    };

    /// <summary>Matches <c>model_dump_json(indent=2)</c>.</summary>
    public static JsonSerializerOptions Indented { get; } = new(Options) { WriteIndented = true };

    /// <summary>
    /// Compare two persisted models by <em>value</em>.
    /// </summary>
    /// <remarks>
    /// The Pydantic originals compare field by field, lists included. The C# records
    /// hold <c>IReadOnlyList</c>/<c>IReadOnlyDictionary</c> members, so the compiler's
    /// generated <c>==</c> falls back to reference equality on exactly those members
    /// — a descriptor that round-tripped through disk is never <c>==</c> to the one
    /// that was saved. Rather than hand-write <c>Equals</c> on a dozen records (and
    /// leave a field out of one of them a year from now), compare the serialised
    /// form: it is derived from the same property set the file is, so it cannot
    /// drift.
    /// </remarks>
    public static bool ValueEquals<T>(T left, T right)
        => string.Equals(
            JsonSerializer.Serialize(left, Options),
            JsonSerializer.Serialize(right, Options),
            StringComparison.Ordinal);
}
