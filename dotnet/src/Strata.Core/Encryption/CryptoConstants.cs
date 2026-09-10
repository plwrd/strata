namespace Strata.Core.Encryption;

/// <summary>
/// Format-critical constants, mirrored from
/// <c>app/infrastructure/encryption/primitives.py</c>. Changing any of these
/// changes the on-disk format and breaks existing workspaces.
/// </summary>
public static class CryptoConstants
{
    public const int KeyBytes = 32;
    public const int NonceBytes = 24;
    public const int TagBytes = 16;
    public const int SaltBytes = 16;

    /// <summary>Algorithm id stored in the object header. 1 = XChaCha20-Poly1305-IETF.</summary>
    public const int AlgXChaCha20Poly1305 = 1;

    // Argon2id parameters, version 1. Roughly 0.5-1s on a 2020-era laptop.
    public const int KdfVersion = 1;
    public const int Argon2TimeCost = 3;
    public const int Argon2MemoryKib = 262_144; // 256 MiB
    public const int Argon2Parallelism = 4;
}
