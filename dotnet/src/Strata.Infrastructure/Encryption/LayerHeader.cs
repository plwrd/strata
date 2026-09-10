using System.Numerics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Strata.Core.Encryption;
using Strata.Core.Errors;
using Strata.Infrastructure.Storage;

namespace Strata.Infrastructure.Encryption;

/// <summary>One envelope around the layer data key.</summary>
public sealed record WrappedKey(KdfParams Kdf, byte[] Nonce, byte[] Ciphertext)
{
    public JsonObject ToJson() => new()
    {
        ["kdf"] = Kdf.ToJson(),
        ["nonce"] = Convert.ToHexString(Nonce).ToLowerInvariant(),
        ["ciphertext"] = Convert.ToHexString(Ciphertext).ToLowerInvariant(),
    };

    public static WrappedKey FromJson(JsonObject raw)
    {
        try
        {
            return new WrappedKey(
                KdfParams.FromJson(raw["kdf"]!.AsObject()),
                Convert.FromHexString(raw["nonce"]!.GetValue<string>()),
                Convert.FromHexString(raw["ciphertext"]!.GetValue<string>()));
        }
        catch (Exception exc) when (exc is NullReferenceException or InvalidOperationException
            or FormatException or ArgumentException)
        {
            throw new DecryptionException("The layer header is not valid.", exc);
        }
    }
}

/// <summary>
/// <c>layer.header</c> — the only unencrypted file in a private layer. Port of
/// <c>app/infrastructure/encryption/layer_header.py</c>.
/// </summary>
/// <remarks>
/// <para>It holds the <em>wrapped</em> layer data key, never the key itself:</para>
/// <code>
/// password     ──Argon2id──▶ KEK_pw ──AEAD-wrap──▶ [LDK]
/// recovery key ─────────────▶ KEK_rk ──AEAD-wrap──▶ [LDK]   (optional, same LDK)
/// </code>
/// <para>
/// Wrapping rather than deriving makes a password change cheap (rewrap, touch no
/// object), makes key rotation possible at all, and keeps a recovery key from being
/// a backdoor — it wraps the same LDK independently, so it grants exactly what the
/// password grants and nothing more. Losing both makes the layer unrecoverable;
/// there is no third door.
/// </para>
/// </remarks>
public sealed class LayerHeader
{
    public const string HeaderFilename = "layer.header";
    public const int HeaderFormatVersion = 1;

    /// <summary>AAD binding the wrap to its purpose, so a password envelope can never
    /// be presented as a recovery envelope (or vice versa) to confuse the KDF.</summary>
    public static ReadOnlySpan<byte> AadPassword => "strata:layer-key:password:v1"u8;

    public static ReadOnlySpan<byte> AadRecovery => "strata:layer-key:recovery:v1"u8;

    public const int RecoveryKeyBytes = 32;

    /// <summary>Crockford-ish: no I, O, 0 or 1 — this is written on paper and typed back.</summary>
    private const string RecoveryAlphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

    private const int RecoveryDigits = 52;

    public int FormatVersion { get; set; } = HeaderFormatVersion;

    public string LayerId { get; set; } = string.Empty;

    public string CreatedAt { get; set; } = string.Empty;

    public string UpdatedAt { get; set; } = string.Empty;

    public int KeyGeneration { get; set; } = 1;

    public string ManifestObjectId { get; set; } = string.Empty;

    public WrappedKey? PasswordEnvelope { get; set; }

    public WrappedKey? RecoveryEnvelope { get; set; }

    public bool PaddingEnabled { get; set; } = true;

    // -- recovery keys -------------------------------------------------------

    /// <summary>A 256-bit recovery key, grouped for transcription.</summary>
    public static string GenerateRecoveryKey()
    {
        var raw = RandomNumberGenerator.GetBytes(RecoveryKeyBytes);
        var value = new BigInteger(raw, isUnsigned: true, isBigEndian: true);
        var digits = new StringBuilder();
        while (value > BigInteger.Zero)
        {
            value = BigInteger.DivRem(value, 32, out var remainder);
            digits.Append(RecoveryAlphabet[(int)remainder]);
        }

        var text = new string(digits.ToString().Reverse().ToArray())
            .PadLeft(RecoveryDigits, RecoveryAlphabet[0]);

        var grouped = new StringBuilder();
        for (var i = 0; i < text.Length; i += 4)
        {
            if (i > 0)
            {
                grouped.Append('-');
            }

            grouped.Append(text.AsSpan(i, Math.Min(4, text.Length - i)));
        }

        return grouped.ToString();
    }

    public static string NormaliseRecoveryKey(string value)
        => value.Replace("-", string.Empty).Replace(" ", string.Empty).Trim().ToUpperInvariant();

    // -- key operations ------------------------------------------------------

    public static WrappedKey WrapKey(string secret, ReadOnlySpan<byte> layerKey, ReadOnlySpan<byte> aad)
    {
        var parameters = KdfParams.New();
        var kek = AeadPrimitives.DeriveKey(secret, parameters);
        try
        {
            var (nonce, ciphertext) = AeadPrimitives.Encrypt(kek, layerKey, aad);
            return new WrappedKey(parameters, nonce, ciphertext);
        }
        finally
        {
            AeadPrimitives.Zeroize(kek);
        }
    }

    public static byte[] UnwrapKey(string secret, WrappedKey wrapped, ReadOnlySpan<byte> aad)
    {
        var kek = AeadPrimitives.DeriveKey(secret, wrapped.Kdf);
        try
        {
            var key = AeadPrimitives.Decrypt(kek, wrapped.Nonce, wrapped.Ciphertext, aad);
            if (key.Length != CryptoConstants.KeyBytes)
            {
                throw new DecryptionException();
            }

            return key;
        }
        finally
        {
            AeadPrimitives.Zeroize(kek);
        }
    }

    /// <summary>Create a header around a <em>fresh random</em> layer key.</summary>
    /// <remarks>The password never becomes the key. It wraps it.</remarks>
    public static (LayerHeader Header, byte[] LayerKey) Create(
        string layerId,
        string password,
        string createdAt,
        string? recoveryKey = null)
    {
        var layerKey = AeadPrimitives.RandomKey();
        var header = new LayerHeader
        {
            LayerId = layerId,
            CreatedAt = createdAt,
            UpdatedAt = createdAt,
            PasswordEnvelope = WrapKey(password, layerKey, AadPassword),
        };

        if (!string.IsNullOrEmpty(recoveryKey))
        {
            header.RecoveryEnvelope = WrapKey(NormaliseRecoveryKey(recoveryKey), layerKey, AadRecovery);
        }

        return (header, layerKey);
    }

    public byte[] UnlockWithPassword(string password)
    {
        if (PasswordEnvelope is null)
        {
            throw new DecryptionException();
        }

        return UnwrapKey(password, PasswordEnvelope, AadPassword);
    }

    public byte[] UnlockWithRecoveryKey(string recoveryKey)
    {
        if (RecoveryEnvelope is null)
        {
            throw new DecryptionException();
        }

        return UnwrapKey(NormaliseRecoveryKey(recoveryKey), RecoveryEnvelope, AadRecovery);
    }

    /// <summary>Rewrap the <em>same</em> layer key. No object is re-encrypted.</summary>
    /// <remarks>
    /// This does not revoke anyone who already has the layer key — it only changes
    /// what unlocks it on this device. Revocation is key <em>rotation</em>, and it is
    /// a different, expensive operation.
    /// </remarks>
    public void ChangePassword(string oldPassword, string newPassword)
    {
        var layerKey = UnlockWithPassword(oldPassword);
        try
        {
            PasswordEnvelope = WrapKey(newPassword, layerKey, AadPassword);
        }
        finally
        {
            AeadPrimitives.Zeroize(layerKey);
        }
    }

    public void SetRecoveryKey(string password, string recoveryKey)
    {
        var layerKey = UnlockWithPassword(password);
        try
        {
            RecoveryEnvelope = WrapKey(NormaliseRecoveryKey(recoveryKey), layerKey, AadRecovery);
        }
        finally
        {
            AeadPrimitives.Zeroize(layerKey);
        }
    }

    /// <summary>Point the envelopes at a new layer key. The caller re-encrypts objects.</summary>
    public void RewrapForRotation(string password, ReadOnlySpan<byte> newLayerKey)
    {
        PasswordEnvelope = WrapKey(password, newLayerKey, AadPassword);

        // A recovery key that still opened the *old* key would be a hole straight
        // through the rotation, so it is dropped and must be re-issued.
        RecoveryEnvelope = null;
        KeyGeneration += 1;
    }

    // -- persistence ---------------------------------------------------------

    public JsonObject ToJson() => new()
    {
        ["format_version"] = FormatVersion,
        ["layer_id"] = LayerId,
        ["created_at"] = CreatedAt,
        ["updated_at"] = UpdatedAt,
        ["key_generation"] = KeyGeneration,
        ["manifest_object_id"] = ManifestObjectId,
        ["padding_enabled"] = PaddingEnabled,
        ["password"] = PasswordEnvelope?.ToJson(),
        ["recovery"] = RecoveryEnvelope?.ToJson(),
    };

    public static LayerHeader FromJson(JsonObject raw)
    {
        var version = raw["format_version"]?.GetValue<int>() ?? 0;
        if (version > HeaderFormatVersion)
        {
            throw new DecryptionException("This layer was created by a newer version of Strata.");
        }

        return new LayerHeader
        {
            FormatVersion = version,
            LayerId = raw["layer_id"]?.GetValue<string>() ?? string.Empty,
            CreatedAt = raw["created_at"]?.GetValue<string>() ?? string.Empty,
            UpdatedAt = raw["updated_at"]?.GetValue<string>() ?? string.Empty,
            KeyGeneration = raw["key_generation"]?.GetValue<int>() ?? 1,
            ManifestObjectId = raw["manifest_object_id"]?.GetValue<string>() ?? string.Empty,
            PaddingEnabled = raw["padding_enabled"]?.GetValue<bool>() ?? true,
            PasswordEnvelope = raw["password"] is JsonObject password ? WrappedKey.FromJson(password) : null,
            RecoveryEnvelope = raw["recovery"] is JsonObject recovery ? WrappedKey.FromJson(recovery) : null,
        };
    }

    public void Save(string root)
    {
        Directory.CreateDirectory(root);
        var json = ToJson().ToJsonString(new JsonSerializerOptions { WriteIndented = true });
        // Atomic: losing the header to a half-written file would lose the layer.
        AtomicFile.WriteText(Path.Combine(root, HeaderFilename), json);
    }

    public static LayerHeader Load(string root)
    {
        var path = Path.Combine(root, HeaderFilename);
        if (!File.Exists(path))
        {
            throw new DecryptionException("This layer has no header.");
        }

        JsonNode? node;
        try
        {
            node = JsonNode.Parse(File.ReadAllText(path));
        }
        catch (Exception exc) when (exc is JsonException or IOException or UnauthorizedAccessException)
        {
            throw new DecryptionException("The layer header could not be read.", exc);
        }

        if (node is not JsonObject raw)
        {
            throw new DecryptionException("The layer header is not valid.");
        }

        return FromJson(raw);
    }
}
