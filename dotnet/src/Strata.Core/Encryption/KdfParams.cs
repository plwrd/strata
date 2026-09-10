using System.Text.Json.Nodes;
using Strata.Core.Errors;

namespace Strata.Core.Encryption;

/// <summary>
/// Everything needed to re-derive a key-encryption key from a password.
/// Port of <c>app/infrastructure/encryption/primitives.py::KdfParams</c>.
/// </summary>
/// <remarks>
/// The parameters are stored with every layer, so they can be raised for new
/// layers (and old layers rewrapped) without a format break.
/// </remarks>
public sealed record KdfParams(
    int Version = CryptoConstants.KdfVersion,
    int TimeCost = CryptoConstants.Argon2TimeCost,
    int MemoryKib = CryptoConstants.Argon2MemoryKib,
    int Parallelism = CryptoConstants.Argon2Parallelism,
    byte[]? Salt = null)
{
    public const string Algorithm = "argon2id";

    public byte[] Salt { get; init; } = Salt ?? [];

    /// <summary>
    /// The cost profile new layers are created with.
    /// </summary>
    /// <remarks>
    /// Settable for tests only, mirroring the Python suite's <c>cheap_kdf</c>
    /// fixture: production derives at 256 MiB per hash, and a suite that creates
    /// dozens of private layers asks for that allocation dozens of times. Only
    /// <em>new</em> layers are affected — the parameters are written into each layer
    /// header and read back from it, so unlock always uses whatever the header says.
    /// <see cref="CryptoConstants"/> holds the production values and is asserted
    /// directly, so a real weakening cannot hide behind this.
    /// </remarks>
    public static KdfParams Defaults { get; set; } = new();

    /// <summary>Fresh parameters at the current defaults, with a random salt.</summary>
    public static KdfParams New() => Defaults with
    {
        Salt = System.Security.Cryptography.RandomNumberGenerator.GetBytes(CryptoConstants.SaltBytes),
    };

    public JsonObject ToJson() => new()
    {
        ["version"] = Version,
        ["algorithm"] = Algorithm,
        ["time_cost"] = TimeCost,
        ["memory_kib"] = MemoryKib,
        ["parallelism"] = Parallelism,
        ["salt"] = Convert.ToHexString(Salt).ToLowerInvariant(),
    };

    public static KdfParams FromJson(JsonObject raw)
    {
        var algorithm = raw["algorithm"]?.GetValue<string>() ?? Algorithm;
        if (algorithm != Algorithm)
        {
            throw new DecryptionException("Unsupported key-derivation algorithm.");
        }

        try
        {
            return new KdfParams(
                Version: raw["version"]!.GetValue<int>(),
                TimeCost: raw["time_cost"]!.GetValue<int>(),
                MemoryKib: raw["memory_kib"]!.GetValue<int>(),
                Parallelism: raw["parallelism"]!.GetValue<int>(),
                Salt: Convert.FromHexString(raw["salt"]!.GetValue<string>()));
        }
        catch (Exception exc) when (exc is NullReferenceException or InvalidOperationException
            or FormatException or ArgumentException)
        {
            // A header we cannot parse is a header we must not guess at: deriving a
            // key from defaulted parameters would silently produce the wrong key.
            throw new DecryptionException("The layer header is not valid.", exc);
        }
    }
}
