using Konscious.Security.Cryptography;

namespace Strata.Infrastructure.Encryption;

/// <summary>
/// Unkeyed BLAKE2b with a caller-chosen digest size, matching Python's
/// <c>hashlib.blake2b(data, digest_size=n)</c>.
/// </summary>
/// <remarks>
/// BLAKE2b is not in the BCL. <c>HMACBlake2B</c> with no key is plain BLAKE2b —
/// the class name reflects that keying is optional, not that a key is applied.
/// </remarks>
public static class Blake2b
{
    public static byte[] Hash(ReadOnlySpan<byte> data, int digestBytes)
    {
        using var hasher = new HMACBlake2B(digestBytes * 8);
        hasher.Initialize();
        return hasher.ComputeHash(data.ToArray());
    }
}
