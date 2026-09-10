using System.Buffers.Binary;
using System.Text;
using Strata.Core.Encryption;
using Strata.Core.Errors;

namespace Strata.Infrastructure.Encryption;

/// <summary>Object types. Bound into the AAD, so a manifest can never be served as a note.</summary>
public static class ObjectTypes
{
    public const byte Manifest = 1;
    public const byte Note = 2;
    public const byte Attachment = 3;
    public const byte Index = 4;
    public const byte Embedding = 5;

    /// <summary>A sealed Yjs binary update.</summary>
    public const byte CrdtUpdate = 6;

    /// <summary>A compacted CRDT base state.</summary>
    public const byte CrdtState = 7;

    private static readonly HashSet<byte> Known =
        [Manifest, Note, Attachment, Index, Embedding, CrdtUpdate, CrdtState];

    public static bool IsKnown(byte objectType) => Known.Contains(objectType);
}

/// <summary>
/// The authenticated 71-byte object header — port of the header half of
/// <c>app/infrastructure/encryption/container.py</c>.
/// </summary>
/// <remarks>
/// <code>
/// offset  size  field
/// 0       7     magic          "STRATA1"
/// 7       1     format_version u8
/// 8       1     algorithm      u8   (1 = XChaCha20-Poly1305)
/// 9       1     object_type    u8
/// 10      1     flags          u8   (bit 0: padded)
/// 11      16    layer_binding  BLAKE2b-128(layer_id)
/// 27      16    object_id      raw 16 bytes (hex form is the filename)
/// 43      24    nonce
/// 67      4     plaintext_len  u32 big-endian (before padding)
/// </code>
/// The whole header is the AAD, so an attacker cannot swap two objects, move one
/// between layers, relabel a note as a manifest, or downgrade the format.
/// </remarks>
public sealed record ObjectHeader(
    byte FormatVersion,
    byte Algorithm,
    byte ObjectType,
    byte Flags,
    byte[] LayerBinding,
    byte[] ObjectId,
    byte[] Nonce,
    uint PlaintextLength)
{
    public byte[] Pack()
    {
        var raw = new byte[ObjectContainer.HeaderSize];
        ObjectContainer.Magic.CopyTo(raw.AsSpan(0));
        raw[7] = FormatVersion;
        raw[8] = Algorithm;
        raw[9] = ObjectType;
        raw[10] = Flags;
        LayerBinding.CopyTo(raw.AsSpan(11));
        ObjectId.CopyTo(raw.AsSpan(27));
        Nonce.CopyTo(raw.AsSpan(43));
        BinaryPrimitives.WriteUInt32BigEndian(raw.AsSpan(67), PlaintextLength);
        return raw;
    }

    public static ObjectHeader Unpack(ReadOnlySpan<byte> raw)
    {
        if (raw.Length < ObjectContainer.HeaderSize)
        {
            throw new DecryptionException("The object is truncated.");
        }

        if (!raw[..7].SequenceEqual(ObjectContainer.Magic))
        {
            throw new DecryptionException("Not a Strata object.");
        }

        var header = new ObjectHeader(
            FormatVersion: raw[7],
            Algorithm: raw[8],
            ObjectType: raw[9],
            Flags: raw[10],
            LayerBinding: raw.Slice(11, 16).ToArray(),
            ObjectId: raw.Slice(27, 16).ToArray(),
            Nonce: raw.Slice(43, 24).ToArray(),
            PlaintextLength: BinaryPrimitives.ReadUInt32BigEndian(raw.Slice(67, 4)));

        if (header.FormatVersion > ObjectContainer.FormatVersion)
        {
            throw new DecryptionException("This object was written by a newer version of Strata.");
        }

        if (header.Algorithm != CryptoConstants.AlgXChaCha20Poly1305)
        {
            throw new DecryptionException("Unsupported encryption algorithm.");
        }

        if (!ObjectTypes.IsKnown(header.ObjectType))
        {
            throw new DecryptionException("Unknown object type.");
        }

        if (header.PlaintextLength > ObjectContainer.MaxPlaintext)
        {
            throw new DecryptionException("The object claims an implausible size.");
        }

        return header;
    }
}

/// <summary>
/// The encrypted object container — one object, one file. Port of
/// <c>app/infrastructure/encryption/container.py</c>.
/// </summary>
/// <remarks>
/// Plaintext is padded into buckets before encryption: AEAD hides content but not
/// length, and lengths leak (a 40-byte note is a title, a 4 MB one is a PDF).
/// <c>plaintext_len</c> records the true size so padding is exactly reversible.
/// What still leaks, honestly: the object count, the <em>bucketed</em> size, file
/// mtimes, and the existence of the layer. See THREAT_MODEL.md.
/// </remarks>
public static class ObjectContainer
{
    public static ReadOnlySpan<byte> Magic => "STRATA1"u8;

    public const byte FormatVersion = 1;
    public const int HeaderSize = 71;
    public const byte FlagPadded = 0b0000_0001;
    public const int ObjectIdBytes = 16;
    public const uint MaxPlaintext = 256 * 1024 * 1024;

    // Past 1 MiB the step is 1 MiB: at that size the content is an attachment and
    // the marginal privacy of finer buckets is not worth the disk.
    private static readonly int[] Buckets = [256, 1024, 4096, 16_384, 65_536, 262_144, 1_048_576];
    private const int LargeStep = 1_048_576;

    /// <summary>
    /// A 16-byte commitment to the layer, for the AAD. Hashed rather than stored raw
    /// so the file does not carry a readable layer identifier, while still binding
    /// the object to exactly one layer.
    /// </summary>
    public static byte[] LayerBinding(string layerId)
        => Blake2b.Hash(Encoding.UTF8.GetBytes(layerId), 16);

    public static long PaddedLength(long length)
    {
        foreach (var bucket in Buckets)
        {
            if (length <= bucket)
            {
                return bucket;
            }
        }

        var steps = (length + LargeStep - 1) / LargeStep;
        return steps * LargeStep;
    }

    /// <summary>Encrypt <paramref name="plaintext"/> into a complete on-disk object.</summary>
    /// <param name="nonce">
    /// Test-only override. Production callers omit it so a fresh random nonce is used.
    /// </param>
    public static byte[] Seal(
        ReadOnlySpan<byte> key,
        string layerId,
        ReadOnlySpan<byte> objectId,
        byte objectType,
        ReadOnlySpan<byte> plaintext,
        bool pad = true,
        ReadOnlySpan<byte> nonce = default)
    {
        if (!ObjectTypes.IsKnown(objectType))
        {
            throw new DecryptionException("Unknown object type.");
        }

        if (objectId.Length != ObjectIdBytes)
        {
            throw new DecryptionException("Object ids are 16 bytes.");
        }

        if (plaintext.Length > MaxPlaintext)
        {
            throw new DecryptionException("The object is too large.");
        }

        byte[] body;
        byte flags = 0;
        if (pad)
        {
            body = new byte[PaddedLength(plaintext.Length)];
            plaintext.CopyTo(body);
            flags |= FlagPadded;
        }
        else
        {
            body = plaintext.ToArray();
        }

        // The nonce goes into the header, and the header is the AAD, so the nonce is
        // authenticated too — an attacker cannot swap it for one that decrypts to
        // something else under a related key.
        var actualNonce = nonce.IsEmpty ? AeadPrimitives.RandomNonce() : nonce.ToArray();
        var header = new ObjectHeader(
            FormatVersion: FormatVersion,
            Algorithm: CryptoConstants.AlgXChaCha20Poly1305,
            ObjectType: objectType,
            Flags: flags,
            LayerBinding: LayerBinding(layerId),
            ObjectId: objectId.ToArray(),
            Nonce: actualNonce,
            PlaintextLength: (uint)plaintext.Length);

        var aad = header.Pack();
        var (_, ciphertext) = AeadPrimitives.Encrypt(key, body, aad, actualNonce);

        var blob = new byte[aad.Length + ciphertext.Length];
        aad.CopyTo(blob.AsSpan());
        ciphertext.CopyTo(blob.AsSpan(aad.Length));
        return blob;
    }

    /// <summary>Verify and decrypt an object. Any mismatch is a <see cref="DecryptionException"/>.</summary>
    public static byte[] OpenSealed(
        ReadOnlySpan<byte> key,
        string layerId,
        ReadOnlySpan<byte> objectId,
        byte expectedType,
        ReadOnlySpan<byte> blob)
    {
        var header = ObjectHeader.Unpack(blob);

        // Belt-and-braces: the AAD already binds all three, so a mismatch would fail
        // authentication anyway. Checking first turns a confusing "decryption failed"
        // into a precise internal error during development, and costs nothing.
        if (!AeadPrimitives.ConstantTimeEquals(header.LayerBinding, LayerBinding(layerId)))
        {
            throw new DecryptionException("This object does not belong to this layer.");
        }

        if (!AeadPrimitives.ConstantTimeEquals(header.ObjectId, objectId))
        {
            throw new DecryptionException("This object is not the one that was requested.");
        }

        if (header.ObjectType != expectedType)
        {
            throw new DecryptionException("This object is not of the expected type.");
        }

        if (header.Nonce.Length != CryptoConstants.NonceBytes)
        {
            throw new DecryptionException();
        }

        var aad = blob[..HeaderSize];
        var ciphertext = blob[HeaderSize..];
        if (ciphertext.IsEmpty)
        {
            throw new DecryptionException("The object is truncated.");
        }

        var body = AeadPrimitives.Decrypt(key, header.Nonce, ciphertext, aad);

        if ((header.Flags & FlagPadded) != 0)
        {
            if (header.PlaintextLength > body.Length)
            {
                throw new DecryptionException("The object's declared length is inconsistent.");
            }

            return body[..(int)header.PlaintextLength];
        }

        return body;
    }
}
