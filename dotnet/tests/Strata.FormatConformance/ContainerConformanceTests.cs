using Strata.Core.Errors;
using Strata.Infrastructure.Encryption;

namespace Strata.FormatConformance;

/// <summary>
/// The sealed-object container against the blob the Python app wrote.
/// </summary>
public class ContainerConformanceTests
{
    private static readonly System.Text.Json.JsonElement Vector = Fixtures.Json("container_note.json");

    private static byte[] Blob => Fixtures.Bytes(Vector.GetProperty("blob_file").GetString()!);

    [Fact]
    public void Seal_reproduces_the_python_blob_byte_for_byte()
    {
        var sealed_ = ObjectContainer.Seal(
            Vector.Hex("key_hex"),
            Vector.GetProperty("layer_id").GetString()!,
            Vector.Hex("object_id_hex"),
            (byte)Vector.GetProperty("object_type").GetInt32(),
            Vector.Hex("plaintext_hex"),
            pad: true,
            nonce: Vector.Hex("nonce_hex"));

        Assert.Equal(Blob, sealed_);
    }

    [Fact]
    public void OpenSealed_recovers_the_plaintext_without_padding()
    {
        var plaintext = ObjectContainer.OpenSealed(
            Vector.Hex("key_hex"),
            Vector.GetProperty("layer_id").GetString()!,
            Vector.Hex("object_id_hex"),
            (byte)Vector.GetProperty("object_type").GetInt32(),
            Blob);

        Assert.Equal(Vector.Hex("plaintext_hex"), plaintext);
    }

    [Fact]
    public void Header_fields_match_the_documented_layout()
    {
        var header = ObjectHeader.Unpack(Blob);

        Assert.Equal(ObjectContainer.FormatVersion, header.FormatVersion);
        Assert.Equal(Strata.Core.Encryption.CryptoConstants.AlgXChaCha20Poly1305, header.Algorithm);
        Assert.Equal(ObjectTypes.Note, header.ObjectType);
        Assert.Equal(ObjectContainer.FlagPadded, header.Flags & ObjectContainer.FlagPadded);
        Assert.Equal(Vector.Hex("layer_binding_hex"), header.LayerBinding);
        Assert.Equal(Vector.Hex("object_id_hex"), header.ObjectId);
        Assert.Equal(Vector.Hex("nonce_hex"), header.Nonce);
        Assert.Equal((uint)Vector.Hex("plaintext_hex").Length, header.PlaintextLength);
        Assert.Equal(header.Pack(), Blob.AsSpan(0, ObjectContainer.HeaderSize).ToArray());
    }

    [Fact]
    public void LayerBinding_is_blake2b_128_of_the_layer_id()
        => Assert.Equal(
            Vector.Hex("layer_binding_hex"),
            ObjectContainer.LayerBinding(Vector.GetProperty("layer_id").GetString()!));

    [Fact]
    public void Blob_is_header_plus_padded_bucket_plus_tag()
        => Assert.Equal(
            ObjectContainer.HeaderSize + Vector.GetProperty("padded_length").GetInt32() + 16,
            Blob.Length);

    // -- negatives -----------------------------------------------------------

    [Fact]
    public void OpenSealed_refuses_another_layer()
    {
        var negatives = Vector.GetProperty("negatives");
        Assert.Throws<DecryptionException>(() => ObjectContainer.OpenSealed(
            Vector.Hex("key_hex"),
            negatives.GetProperty("wrong_layer_id").GetString()!,
            Vector.Hex("object_id_hex"),
            ObjectTypes.Note,
            Blob));
    }

    [Fact]
    public void OpenSealed_refuses_another_object_id()
    {
        var negatives = Vector.GetProperty("negatives");
        Assert.Throws<DecryptionException>(() => ObjectContainer.OpenSealed(
            Vector.Hex("key_hex"),
            Vector.GetProperty("layer_id").GetString()!,
            negatives.Hex("wrong_object_id_hex"),
            ObjectTypes.Note,
            Blob));
    }

    [Fact]
    public void OpenSealed_refuses_a_relabelled_type()
        => Assert.Throws<DecryptionException>(() => ObjectContainer.OpenSealed(
            Vector.Hex("key_hex"),
            Vector.GetProperty("layer_id").GetString()!,
            Vector.Hex("object_id_hex"),
            ObjectTypes.Manifest,
            Blob));

    [Fact]
    public void OpenSealed_refuses_a_truncated_header()
    {
        var truncated = Blob.AsSpan(0, Vector.GetProperty("negatives").GetProperty("truncated_len").GetInt32()).ToArray();
        Assert.Throws<DecryptionException>(() => ObjectContainer.OpenSealed(
            Vector.Hex("key_hex"),
            Vector.GetProperty("layer_id").GetString()!,
            Vector.Hex("object_id_hex"),
            ObjectTypes.Note,
            truncated));
    }

    [Fact]
    public void OpenSealed_refuses_a_header_with_no_ciphertext()
    {
        var headerOnly = Blob.AsSpan(0, ObjectContainer.HeaderSize).ToArray();
        Assert.Throws<DecryptionException>(() => ObjectContainer.OpenSealed(
            Vector.Hex("key_hex"),
            Vector.GetProperty("layer_id").GetString()!,
            Vector.Hex("object_id_hex"),
            ObjectTypes.Note,
            headerOnly));
    }

    [Fact]
    public void OpenSealed_refuses_a_tampered_header_byte()
    {
        // The header is the AAD, so flipping a flag bit must fail authentication even
        // though every explicit field check still passes.
        var tampered = Blob.ToArray();
        tampered[10] ^= 0b0000_0010;

        Assert.Throws<DecryptionException>(() => ObjectContainer.OpenSealed(
            Vector.Hex("key_hex"),
            Vector.GetProperty("layer_id").GetString()!,
            Vector.Hex("object_id_hex"),
            ObjectTypes.Note,
            tampered));
    }

    [Fact]
    public void Unpack_refuses_foreign_magic()
    {
        var tampered = Blob.ToArray();
        tampered[0] = (byte)'X';
        Assert.Throws<DecryptionException>(() => ObjectHeader.Unpack(tampered));
    }

    [Fact]
    public void Unpack_refuses_a_newer_format_version()
    {
        var tampered = Blob.ToArray();
        tampered[7] = ObjectContainer.FormatVersion + 1;
        Assert.Throws<DecryptionException>(() => ObjectHeader.Unpack(tampered));
    }

    [Fact]
    public void Unpack_refuses_an_unknown_algorithm()
    {
        var tampered = Blob.ToArray();
        tampered[8] = 9;
        Assert.Throws<DecryptionException>(() => ObjectHeader.Unpack(tampered));
    }

    [Fact]
    public void Unpack_refuses_an_unknown_object_type()
    {
        var tampered = Blob.ToArray();
        tampered[9] = 99;
        Assert.Throws<DecryptionException>(() => ObjectHeader.Unpack(tampered));
    }

    [Fact]
    public void Unpack_refuses_an_implausible_declared_size()
    {
        var tampered = Blob.ToArray();
        tampered[67] = 0xFF;
        Assert.Throws<DecryptionException>(() => ObjectHeader.Unpack(tampered));
    }

    // -- padding ladder ------------------------------------------------------

    [Theory]
    [InlineData(0, 256)]
    [InlineData(256, 256)]
    [InlineData(257, 1024)]
    [InlineData(1024, 1024)]
    [InlineData(1025, 4096)]
    [InlineData(65_536, 65_536)]
    [InlineData(262_145, 1_048_576)]
    [InlineData(1_048_576, 1_048_576)]
    [InlineData(1_048_577, 2_097_152)]
    [InlineData(3_000_000, 3_145_728)]
    public void PaddedLength_matches_the_python_ladder(long length, long expected)
        => Assert.Equal(expected, ObjectContainer.PaddedLength(length));

    [Fact]
    public void Roundtrip_holds_for_every_object_type_with_random_nonces()
    {
        var key = AeadPrimitives.RandomKey();
        var objectId = Convert.FromHexString("0123456789abcdef0123456789abcdef");

        foreach (var type in new byte[]
        {
            ObjectTypes.Manifest, ObjectTypes.Note, ObjectTypes.Attachment, ObjectTypes.Index,
            ObjectTypes.Embedding, ObjectTypes.CrdtUpdate, ObjectTypes.CrdtState,
        })
        {
            var plaintext = System.Text.Encoding.UTF8.GetBytes($"payload for type {type}");
            var blob = ObjectContainer.Seal(key, "layer-x", objectId, type, plaintext);
            Assert.Equal(plaintext, ObjectContainer.OpenSealed(key, "layer-x", objectId, type, blob));
        }
    }

    [Fact]
    public void Unpadded_objects_roundtrip_and_carry_no_padded_flag()
    {
        var key = AeadPrimitives.RandomKey();
        var objectId = Convert.FromHexString("0123456789abcdef0123456789abcdef");
        var plaintext = "no padding here"u8.ToArray();

        var blob = ObjectContainer.Seal(key, "layer-x", objectId, ObjectTypes.Note, plaintext, pad: false);

        Assert.Equal(0, ObjectHeader.Unpack(blob).Flags & ObjectContainer.FlagPadded);
        Assert.Equal(ObjectContainer.HeaderSize + plaintext.Length + 16, blob.Length);
        Assert.Equal(plaintext, ObjectContainer.OpenSealed(key, "layer-x", objectId, ObjectTypes.Note, blob));
    }

    [Fact]
    public void Seal_refuses_an_object_id_that_is_not_16_bytes()
        => Assert.Throws<DecryptionException>(() => ObjectContainer.Seal(
            AeadPrimitives.RandomKey(), "layer-x", new byte[15], ObjectTypes.Note, "x"u8));

    [Fact]
    public void Seal_refuses_an_unknown_object_type()
        => Assert.Throws<DecryptionException>(() => ObjectContainer.Seal(
            AeadPrimitives.RandomKey(), "layer-x", new byte[16], 99, "x"u8));

    [Fact]
    public void Two_seals_of_the_same_plaintext_differ_because_the_nonce_is_random()
    {
        var key = AeadPrimitives.RandomKey();
        var objectId = new byte[16];
        var first = ObjectContainer.Seal(key, "layer-x", objectId, ObjectTypes.Note, "same"u8);
        var second = ObjectContainer.Seal(key, "layer-x", objectId, ObjectTypes.Note, "same"u8);

        Assert.NotEqual(first, second);
    }
}
