using System.Security.Cryptography;
using System.Text;
using Konscious.Security.Cryptography;
using Strata.Core.Encryption;
using Strata.Core.Errors;

namespace Strata.Infrastructure.Encryption;

/// <summary>
/// Cryptographic primitives — port of
/// <c>app/infrastructure/encryption/primitives.py</c>.
/// </summary>
/// <remarks>
/// <para>
/// Choices, and why (ADR-0005), unchanged from Python because the on-disk format
/// depends on them:
/// </para>
/// <list type="bullet">
/// <item><b>XChaCha20-Poly1305</b> for authenticated encryption. Its 192-bit nonce
/// is large enough that random nonces are safe, so we never need a counter — and
/// therefore never need counter state a crash could roll back.</item>
/// <item><b>Argon2id</b> for the password KDF. Parameters are versioned and stored.</item>
/// <item>Keys are <b>random</b>, never derived from a password. The password unlocks
/// the key; it never <em>is</em> the key.</item>
/// </list>
/// <para>
/// .NET has no XChaCha20 (the 24-byte-nonce variant). Rather than take a native
/// libsodium dependency, this composes the two published halves of the same
/// construction: HChaCha20 derives a subkey from the first 16 nonce bytes, and the
/// in-box <see cref="ChaCha20Poly1305"/> (RFC 8439, 12-byte nonce) then runs with
/// nonce <c>0x00000000 || nonce[16..24]</c>. That is XChaCha20-Poly1305-IETF by
/// definition, and the committed KATs in <c>tests/fixtures/format</c> prove it
/// byte-for-byte against libsodium's output.
/// </para>
/// </remarks>
public static class AeadPrimitives
{
    /// <summary>A fresh 256-bit key from the OS CSPRNG.</summary>
    public static byte[] RandomKey() => RandomNumberGenerator.GetBytes(CryptoConstants.KeyBytes);

    /// <summary>
    /// A fresh 192-bit nonce. Random, not counter-based: with 24 bytes the collision
    /// probability is negligible, and a counter would need persistent state that a
    /// crash or a restore from backup could rewind.
    /// </summary>
    public static byte[] RandomNonce() => RandomNumberGenerator.GetBytes(CryptoConstants.NonceBytes);

    /// <summary>Derive a key-encryption key from a password with Argon2id.</summary>
    public static byte[] DeriveKey(string password, KdfParams parameters)
    {
        if (parameters.Salt.Length == 0)
        {
            throw new DecryptionException("Missing key-derivation salt.");
        }

        using var argon = new Argon2id(Encoding.UTF8.GetBytes(password))
        {
            Salt = parameters.Salt,
            Iterations = parameters.TimeCost,
            MemorySize = parameters.MemoryKib,
            DegreeOfParallelism = parameters.Parallelism,
        };
        return argon.GetBytes(CryptoConstants.KeyBytes);
    }

    /// <summary>Encrypt, binding <paramref name="aad"/>. Returns (nonce, ciphertext||tag).</summary>
    public static (byte[] Nonce, byte[] Ciphertext) Encrypt(
        ReadOnlySpan<byte> key,
        ReadOnlySpan<byte> plaintext,
        ReadOnlySpan<byte> aad,
        ReadOnlySpan<byte> nonce = default)
    {
        if (key.Length != CryptoConstants.KeyBytes)
        {
            throw new DecryptionException("Invalid key length.");
        }

        var actualNonce = nonce.IsEmpty ? RandomNonce() : nonce.ToArray();
        if (actualNonce.Length != CryptoConstants.NonceBytes)
        {
            throw new DecryptionException("Invalid nonce length.");
        }

        var subKey = HChaCha20(key, actualNonce.AsSpan(0, 16));
        try
        {
            var output = new byte[plaintext.Length + CryptoConstants.TagBytes];
            using var aead = new ChaCha20Poly1305(subKey);
            aead.Encrypt(
                IetfNonce(actualNonce),
                plaintext,
                output.AsSpan(0, plaintext.Length),
                output.AsSpan(plaintext.Length),
                aad);
            return (actualNonce, output);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(subKey);
        }
    }

    /// <summary>Decrypt and verify. Raises <see cref="DecryptionException"/> on any failure.</summary>
    public static byte[] Decrypt(
        ReadOnlySpan<byte> key,
        ReadOnlySpan<byte> nonce,
        ReadOnlySpan<byte> ciphertext,
        ReadOnlySpan<byte> aad)
    {
        if (key.Length != CryptoConstants.KeyBytes || nonce.Length != CryptoConstants.NonceBytes)
        {
            throw new DecryptionException();
        }

        if (ciphertext.Length < CryptoConstants.TagBytes)
        {
            throw new DecryptionException();
        }

        var subKey = HChaCha20(key, nonce[..16]);
        try
        {
            var plaintext = new byte[ciphertext.Length - CryptoConstants.TagBytes];
            using var aead = new ChaCha20Poly1305(subKey);
            aead.Decrypt(
                IetfNonce(nonce),
                ciphertext[..plaintext.Length],
                ciphertext[plaintext.Length..],
                plaintext,
                aad);
            return plaintext;
        }
        catch (Exception exc) when (exc is CryptographicException or ArgumentException)
        {
            // Never leak which check failed, and never let the library's message out.
            throw new DecryptionException(innerException: exc);
        }
        finally
        {
            CryptographicOperations.ZeroMemory(subKey);
        }
    }

    /// <summary>Overwrite key material in place. Same honest limits as the Python note.</summary>
    public static void Zeroize(byte[] buffer) => CryptographicOperations.ZeroMemory(buffer);

    public static bool ConstantTimeEquals(ReadOnlySpan<byte> left, ReadOnlySpan<byte> right)
        => CryptographicOperations.FixedTimeEquals(left, right);

    // -- XChaCha20 construction ---------------------------------------------

    /// <summary>The IETF 12-byte nonce for the subkey: four zero bytes, then nonce[16..24].</summary>
    private static byte[] IetfNonce(ReadOnlySpan<byte> nonce24)
    {
        var ietf = new byte[12];
        nonce24.Slice(16, 8).CopyTo(ietf.AsSpan(4));
        return ietf;
    }

    /// <summary>
    /// HChaCha20 (draft-irtf-cfrg-xchacha §2.2): the ChaCha20 permutation over
    /// key + 16 nonce bytes, emitting words 0-3 and 12-15 <em>without</em> the final
    /// feed-forward addition. That omission is what makes it a PRF rather than a
    /// stream cipher block, and it is why the result is safe as a subkey.
    /// </summary>
    private static byte[] HChaCha20(ReadOnlySpan<byte> key, ReadOnlySpan<byte> nonce16)
    {
        Span<uint> state =
        [
            0x61707865, 0x3320646e, 0x79622d32, 0x6b206574,
            0, 0, 0, 0, 0, 0, 0, 0,
            0, 0, 0, 0,
        ];

        for (var i = 0; i < 8; i++)
        {
            state[4 + i] = BitConverter.ToUInt32(key.Slice(i * 4, 4));
        }

        for (var i = 0; i < 4; i++)
        {
            state[12 + i] = BitConverter.ToUInt32(nonce16.Slice(i * 4, 4));
        }

        for (var round = 0; round < 10; round++)
        {
            QuarterRound(state, 0, 4, 8, 12);
            QuarterRound(state, 1, 5, 9, 13);
            QuarterRound(state, 2, 6, 10, 14);
            QuarterRound(state, 3, 7, 11, 15);
            QuarterRound(state, 0, 5, 10, 15);
            QuarterRound(state, 1, 6, 11, 12);
            QuarterRound(state, 2, 7, 8, 13);
            QuarterRound(state, 3, 4, 9, 14);
        }

        var subKey = new byte[CryptoConstants.KeyBytes];
        for (var i = 0; i < 4; i++)
        {
            BitConverter.TryWriteBytes(subKey.AsSpan(i * 4), state[i]);
            BitConverter.TryWriteBytes(subKey.AsSpan(16 + (i * 4)), state[12 + i]);
        }

        return subKey;
    }

    private static void QuarterRound(Span<uint> x, int a, int b, int c, int d)
    {
        x[a] += x[b]; x[d] = System.Numerics.BitOperations.RotateLeft(x[d] ^ x[a], 16);
        x[c] += x[d]; x[b] = System.Numerics.BitOperations.RotateLeft(x[b] ^ x[c], 12);
        x[a] += x[b]; x[d] = System.Numerics.BitOperations.RotateLeft(x[d] ^ x[a], 8);
        x[c] += x[d]; x[b] = System.Numerics.BitOperations.RotateLeft(x[b] ^ x[c], 7);
    }
}

/// <summary>Instance adapter over <see cref="AeadPrimitives"/> for injection.</summary>
public sealed class XChaCha20Poly1305Aead : IAeadPrimitives
{
    public (byte[] Nonce, byte[] Ciphertext) Encrypt(
        ReadOnlySpan<byte> key,
        ReadOnlySpan<byte> plaintext,
        ReadOnlySpan<byte> aad,
        ReadOnlySpan<byte> nonce = default)
        => AeadPrimitives.Encrypt(key, plaintext, aad, nonce);

    public byte[] Decrypt(
        ReadOnlySpan<byte> key,
        ReadOnlySpan<byte> nonce,
        ReadOnlySpan<byte> ciphertext,
        ReadOnlySpan<byte> aad)
        => AeadPrimitives.Decrypt(key, nonce, ciphertext, aad);

    public byte[] DeriveKey(string password, KdfParams parameters)
        => AeadPrimitives.DeriveKey(password, parameters);
}
