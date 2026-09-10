using System.Security.Cryptography;

namespace Strata.Core;

/// <summary>
/// Opaque identifiers — port of <c>app/domain/ids.py</c>.
/// </summary>
/// <remarks>
/// Every identifier that crosses the bridge is opaque: it carries no filesystem
/// path, no title, and no information about the object it names. Private-layer
/// object ids are additionally random (never derived from content or names) so the
/// on-disk filename leaks nothing — see ADR-0004.
/// </remarks>
public static class Ids
{
    private const int ObjectIdBytes = 16;
    private const int ShortIdBytes = 8;

    private static string Hex(int bytes)
        => Convert.ToHexString(RandomNumberGenerator.GetBytes(bytes)).ToLowerInvariant();

    /// <summary>A random 128-bit opaque object id as 32 lowercase hex characters.</summary>
    public static string NewObjectId() => Hex(ObjectIdBytes);

    public static string NewLayerId() => $"layer_{Hex(ShortIdBytes)}";

    public static string NewWorkspaceId() => $"ws_{Hex(ShortIdBytes)}";

    public static string NewJobId() => $"job_{Hex(ShortIdBytes)}";

    public static string NewRequestId() => $"req_{Hex(ShortIdBytes)}";

    public static string NewExportId() => $"exp_{Hex(ShortIdBytes)}";

    public static string NewExecutionId() => $"exec_{Hex(ShortIdBytes)}";

    /// <summary>
    /// The two-character storage shard for an object id. Private layers store
    /// objects at <c>objects/&lt;shard&gt;/&lt;object_id&gt;</c>.
    /// </summary>
    public static string ShardFor(string objectId)
        => objectId.Length < 2
            ? throw new ArgumentException("object id too short to shard", nameof(objectId))
            : objectId[..2];
}
