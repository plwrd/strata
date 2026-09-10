using Strata.Core.Errors;
using Strata.Core.Logging;

namespace Strata.Infrastructure.Encryption;

/// <summary>
/// The unlocked-key holder — port of
/// <c>app/infrastructure/encryption/keyholder.py</c>.
/// </summary>
/// <remarks>
/// <para>
/// "Locked" is not a UI state. It is the absence of a key in this object.
/// Everything that can read a private layer must ask here, and locking removes the
/// key — so a component that forgot to check the lock state simply cannot get one.
/// </para>
/// <para>
/// Zeroisation is best-effort and is documented as such (THREAT_MODEL.md, T-06).
/// The key is held in a mutable array so it <em>can</em> be overwritten, but the
/// runtime may already have copied it and the OS may have paged it out. This
/// narrows the window; it does not eliminate it.
/// </para>
/// </remarks>
public sealed class KeyHolder(IStrataLogger? logger = null)
{
    private readonly Dictionary<string, byte[]> _keys = new(StringComparer.Ordinal);
    private readonly IStrataLogger _logger = logger ?? NullLogger.Instance;

    public void Unlock(string layerId, ReadOnlySpan<byte> key)
    {
        Lock(layerId); // never leave an old key behind
        _keys[layerId] = key.ToArray();
        _logger.Info("layer.unlocked", ("layer_id", layerId));
    }

    public byte[] KeyFor(string layerId)
    {
        if (!_keys.TryGetValue(layerId, out var key))
        {
            // Generic on purpose: it does not say whether the layer exists.
            throw new LayerLockedException(
                "This layer is locked.",
                new Dictionary<string, object?> { ["layerId"] = layerId });
        }

        return (byte[])key.Clone();
    }

    public bool IsUnlocked(string layerId) => _keys.ContainsKey(layerId);

    public IReadOnlyList<string> UnlockedLayers() => [.. _keys.Keys];

    public bool Lock(string layerId)
    {
        if (!_keys.Remove(layerId, out var key))
        {
            return false;
        }

        AeadPrimitives.Zeroize(key);
        _logger.Info("layer.locked", ("layer_id", layerId));
        return true;
    }

    public int LockAll() => UnlockedLayers().Count(Lock);
}
