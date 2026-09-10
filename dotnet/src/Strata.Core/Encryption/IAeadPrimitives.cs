namespace Strata.Core.Encryption;

/// <summary>
/// The one seam through which Strata reaches a cipher. Nothing above this
/// interface calls a primitive directly, so there is exactly one place to audit
/// and exactly one place a mistake can live (mirrors the Python module
/// <c>app/infrastructure/encryption/primitives.py</c>).
/// </summary>
public interface IAeadPrimitives
{
    /// <summary>Encrypt, binding <paramref name="aad"/>. Returns nonce + ciphertext||tag.</summary>
    /// <param name="nonce">
    /// Supply only for known-answer tests. Production callers pass <c>null</c> so a
    /// fresh 192-bit random nonce is used — reusing a nonce is the one failure this
    /// construction cannot survive.
    /// </param>
    (byte[] Nonce, byte[] Ciphertext) Encrypt(
        ReadOnlySpan<byte> key,
        ReadOnlySpan<byte> plaintext,
        ReadOnlySpan<byte> aad,
        ReadOnlySpan<byte> nonce = default);

    /// <summary>Decrypt and verify. Throws <see cref="Errors.DecryptionException"/> on any failure.</summary>
    byte[] Decrypt(
        ReadOnlySpan<byte> key,
        ReadOnlySpan<byte> nonce,
        ReadOnlySpan<byte> ciphertext,
        ReadOnlySpan<byte> aad);

    /// <summary>Derive a key-encryption key from a password with Argon2id.</summary>
    byte[] DeriveKey(string password, KdfParams parameters);
}
