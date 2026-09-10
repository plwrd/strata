using Strata.Core.Encryption;
using Strata.Core.Errors;
using Strata.Infrastructure.Encryption;

namespace Strata.FormatConformance;

/// <summary>
/// XChaCha20-Poly1305-IETF and Argon2id against libsodium/argon2-cffi output.
/// A failure here means the .NET port cannot read a workspace the Python app wrote.
/// </summary>
public class PrimitivesConformanceTests
{
    [Fact]
    public void Encrypt_matches_the_libsodium_vector_byte_for_byte()
    {
        var vector = Fixtures.Json("primitives.json");

        var (nonce, ciphertext) = AeadPrimitives.Encrypt(
            vector.Hex("key_hex"),
            vector.Hex("plaintext_hex"),
            vector.Hex("aad_hex"),
            vector.Hex("nonce_hex"));

        Assert.Equal(vector.Hex("nonce_hex"), nonce);
        Assert.Equal(vector.Hex("ciphertext_with_tag_hex"), ciphertext);
    }

    [Fact]
    public void Decrypt_recovers_the_vector_plaintext()
    {
        var vector = Fixtures.Json("primitives.json");

        var plaintext = AeadPrimitives.Decrypt(
            vector.Hex("key_hex"),
            vector.Hex("nonce_hex"),
            vector.Hex("ciphertext_with_tag_hex"),
            vector.Hex("aad_hex"));

        Assert.Equal(vector.Hex("plaintext_hex"), plaintext);
    }

    [Fact]
    public void Decrypt_refuses_a_flipped_ciphertext_bit()
    {
        var vector = Fixtures.Json("primitives.json");
        var tampered = vector.Hex("ciphertext_with_tag_hex");
        tampered[0] ^= 0x01;

        Assert.Throws<DecryptionException>(() => AeadPrimitives.Decrypt(
            vector.Hex("key_hex"), vector.Hex("nonce_hex"), tampered, vector.Hex("aad_hex")));
    }

    [Fact]
    public void Decrypt_refuses_a_different_aad()
    {
        var vector = Fixtures.Json("primitives.json");

        Assert.Throws<DecryptionException>(() => AeadPrimitives.Decrypt(
            vector.Hex("key_hex"),
            vector.Hex("nonce_hex"),
            vector.Hex("ciphertext_with_tag_hex"),
            "strata:kat:aad:v2"u8));
    }

    [Theory]
    [InlineData(31)]
    [InlineData(33)]
    public void Encrypt_refuses_a_key_that_is_not_256_bits(int keyLength)
    {
        Assert.Throws<DecryptionException>(() => AeadPrimitives.Encrypt(
            new byte[keyLength], "x"u8, ReadOnlySpan<byte>.Empty, new byte[CryptoConstants.NonceBytes]));
    }

    [Fact]
    public void Encrypt_refuses_a_nonce_that_is_not_192_bits()
    {
        Assert.Throws<DecryptionException>(() => AeadPrimitives.Encrypt(
            new byte[CryptoConstants.KeyBytes], "x"u8, ReadOnlySpan<byte>.Empty, new byte[12]));
    }

    [Fact]
    public void Random_nonces_do_not_repeat()
    {
        var seen = new HashSet<string>();
        for (var i = 0; i < 256; i++)
        {
            Assert.True(seen.Add(Convert.ToHexString(AeadPrimitives.RandomNonce())));
        }
    }

    [Fact]
    public void DeriveKey_matches_argon2_cffi_for_the_fixture_parameters()
    {
        var vector = Fixtures.Json("layer_header.json");
        var envelope = vector.GetProperty("header").GetProperty("password");
        var kdf = envelope.GetProperty("kdf");

        var parameters = new KdfParams(
            Version: kdf.GetProperty("version").GetInt32(),
            TimeCost: kdf.GetProperty("time_cost").GetInt32(),
            MemoryKib: kdf.GetProperty("memory_kib").GetInt32(),
            Parallelism: kdf.GetProperty("parallelism").GetInt32(),
            Salt: kdf.Hex("salt"));

        var kek = AeadPrimitives.DeriveKey(vector.GetProperty("password").GetString()!, parameters);

        // The KEK is not published on its own; that it unwraps the layer key proves it.
        var layerKey = AeadPrimitives.Decrypt(
            kek, envelope.Hex("nonce"), envelope.Hex("ciphertext"), LayerHeader.AadPassword);

        Assert.Equal(vector.Hex("layer_key_hex"), layerKey);
    }

    [Fact]
    public void DeriveKey_refuses_an_empty_salt()
    {
        Assert.Throws<DecryptionException>(
            () => AeadPrimitives.DeriveKey("pw", new KdfParams(TimeCost: 1, MemoryKib: 8, Parallelism: 1)));
    }
}
