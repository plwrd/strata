using System.Text.Json;
using System.Text.Json.Nodes;
using Strata.Core.Encryption;
using Strata.Core.Errors;
using Strata.Infrastructure.Encryption;

namespace Strata.FormatConformance;

/// <summary>
/// <c>layer.header</c> wrap/unwrap against the Python fixture, plus the couplings
/// that keep a recovery key from becoming a backdoor.
/// </summary>
public class LayerHeaderConformanceTests
{
    private static readonly JsonElement Vector = Fixtures.Json("layer_header.json");

    private static LayerHeader LoadFixtureHeader()
        => LayerHeader.FromJson(JsonNode.Parse(Vector.GetProperty("header").GetRawText())!.AsObject());

    /// <summary>Fixture costs: t=1 m=8 p=1. Production is t=3 m=256MiB p=4.</summary>
    private static KdfParams FixtureKdf() => LoadFixtureHeader().PasswordEnvelope!.Kdf;

    [Fact]
    public void Password_envelope_unwraps_the_fixture_layer_key()
    {
        var key = LoadFixtureHeader().UnlockWithPassword(Vector.GetProperty("password").GetString()!);
        Assert.Equal(Vector.Hex("layer_key_hex"), key);
    }

    [Fact]
    public void Recovery_envelope_unwraps_the_same_layer_key()
    {
        var key = LoadFixtureHeader()
            .UnlockWithRecoveryKey(Vector.GetProperty("recovery_normalised").GetString()!);
        Assert.Equal(Vector.Hex("layer_key_hex"), key);
    }

    [Fact]
    public void Recovery_key_is_accepted_in_its_grouped_written_form()
    {
        var normalised = Vector.GetProperty("recovery_normalised").GetString()!;
        var grouped = string.Join('-', Enumerable.Range(0, (normalised.Length + 3) / 4)
            .Select(i => normalised.Substring(i * 4, Math.Min(4, normalised.Length - (i * 4)))));

        Assert.Equal(Vector.Hex("layer_key_hex"), LoadFixtureHeader().UnlockWithRecoveryKey(grouped));
    }

    [Fact]
    public void Wrong_password_is_refused()
        => Assert.Throws<DecryptionException>(() => LoadFixtureHeader().UnlockWithPassword("wrong"));

    [Fact]
    public void A_password_envelope_cannot_be_presented_as_a_recovery_envelope()
    {
        // Same ciphertext, different purpose AAD: the swap must fail.
        var header = LoadFixtureHeader();
        header.RecoveryEnvelope = header.PasswordEnvelope;

        Assert.Throws<DecryptionException>(
            () => header.UnlockWithRecoveryKey(Vector.GetProperty("password").GetString()!));
    }

    [Fact]
    public void Purpose_aads_match_the_python_constants()
    {
        Assert.Equal(Vector.Hex("aad_password_hex"), LayerHeader.AadPassword.ToArray());
        Assert.Equal(Vector.Hex("aad_recovery_hex"), LayerHeader.AadRecovery.ToArray());
    }

    [Fact]
    public void Json_roundtrips_through_the_python_shape()
    {
        var header = LoadFixtureHeader();
        var json = header.ToJson();

        Assert.Equal(1, json["format_version"]!.GetValue<int>());
        Assert.Equal("layer-kat-0001", json["layer_id"]!.GetValue<string>());
        Assert.Equal(
            Vector.GetProperty("header").GetProperty("password").GetProperty("ciphertext").GetString(),
            json["password"]!["ciphertext"]!.GetValue<string>());
        Assert.Equal("argon2id", json["password"]!["kdf"]!["algorithm"]!.GetValue<string>());

        var reparsed = LayerHeader.FromJson(json);
        Assert.Equal(header.ManifestObjectId, reparsed.ManifestObjectId);
        Assert.Equal(
            Vector.Hex("layer_key_hex"),
            reparsed.UnlockWithPassword(Vector.GetProperty("password").GetString()!));
    }

    [Fact]
    public void A_header_from_a_newer_format_version_is_refused()
    {
        var json = LoadFixtureHeader().ToJson();
        json["format_version"] = LayerHeader.HeaderFormatVersion + 1;

        Assert.Throws<DecryptionException>(() => LayerHeader.FromJson(json));
    }

    [Fact]
    public void Save_and_load_roundtrip_on_disk()
    {
        var directory = Path.Combine(Path.GetTempPath(), "strata-kat-" + Guid.NewGuid().ToString("N"));
        try
        {
            LoadFixtureHeader().Save(directory);
            Assert.True(File.Exists(Path.Combine(directory, LayerHeader.HeaderFilename)));

            var loaded = LayerHeader.Load(directory);
            Assert.Equal(
                Vector.Hex("layer_key_hex"),
                loaded.UnlockWithPassword(Vector.GetProperty("password").GetString()!));
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    [Fact]
    public void Load_without_a_header_file_is_refused()
        => Assert.Throws<DecryptionException>(() => LayerHeader.Load(Path.GetTempPath()));

    // -- key lifecycle (fixture KDF costs, so these stay fast) ---------------

    [Fact]
    public void Changing_the_password_rewraps_the_same_layer_key()
    {
        var header = LoadFixtureHeader();
        var original = header.UnlockWithPassword(Vector.GetProperty("password").GetString()!);

        header.ChangePassword(Vector.GetProperty("password").GetString()!, "a new password");

        Assert.Equal(original, header.UnlockWithPassword("a new password"));
        Assert.Throws<DecryptionException>(
            () => header.UnlockWithPassword(Vector.GetProperty("password").GetString()!));
    }

    [Fact]
    public void Rotation_drops_the_recovery_envelope_and_bumps_the_generation()
    {
        var header = LoadFixtureHeader();
        var generation = header.KeyGeneration;
        var newKey = AeadPrimitives.RandomKey();

        header.RewrapForRotation(Vector.GetProperty("password").GetString()!, newKey);

        Assert.Null(header.RecoveryEnvelope);
        Assert.Equal(generation + 1, header.KeyGeneration);
        Assert.Equal(newKey, header.UnlockWithPassword(Vector.GetProperty("password").GetString()!));
    }

    [Fact]
    public void Unlocking_a_header_with_no_envelope_is_refused()
    {
        var header = new LayerHeader();
        Assert.Throws<DecryptionException>(() => header.UnlockWithPassword("pw"));
        Assert.Throws<DecryptionException>(() => header.UnlockWithRecoveryKey("AAAA-BBBB"));
    }

    [Fact]
    public void Wrap_and_unwrap_bind_the_purpose_aad()
    {
        var layerKey = AeadPrimitives.RandomKey();
        var kek = AeadPrimitives.DeriveKey("pw", FixtureKdf());
        var (nonce, ciphertext) = AeadPrimitives.Encrypt(kek, layerKey, LayerHeader.AadPassword);
        var wrapped = new WrappedKey(FixtureKdf(), nonce, ciphertext);

        Assert.Equal(layerKey, LayerHeader.UnwrapKey("pw", wrapped, LayerHeader.AadPassword));
        Assert.Throws<DecryptionException>(
            () => LayerHeader.UnwrapKey("pw", wrapped, LayerHeader.AadRecovery));
    }

    // -- recovery key format -------------------------------------------------

    [Fact]
    public void Generated_recovery_keys_are_52_characters_in_13_groups_of_4()
    {
        for (var i = 0; i < 32; i++)
        {
            var key = LayerHeader.GenerateRecoveryKey();
            var groups = key.Split('-');

            Assert.Equal(13, groups.Length);
            Assert.All(groups, group => Assert.Equal(4, group.Length));
            Assert.Equal(52, LayerHeader.NormaliseRecoveryKey(key).Length);

            // Crockford-ish alphabet: nothing a human confuses when copying by hand.
            Assert.DoesNotContain(LayerHeader.NormaliseRecoveryKey(key), c => c is 'I' or 'O' or '0' or '1');
        }
    }

    [Theory]
    [InlineData("abcd-efgh", "ABCDEFGH")]
    [InlineData("  ABCD EFGH  ", "ABCDEFGH")]
    [InlineData("ABCDEFGH", "ABCDEFGH")]
    public void Normalising_a_recovery_key_strips_grouping_and_case(string input, string expected)
        => Assert.Equal(expected, LayerHeader.NormaliseRecoveryKey(input));
}
