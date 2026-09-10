using System.Text.Json;
using System.Text.Json.Nodes;
using Strata.Core.Errors;
using Strata.Infrastructure.Encryption;

namespace Strata.FormatConformance;

/// <summary>
/// The rotation journal against the Python fixture. A journal the .NET side cannot
/// read is a workspace stuck half-rotated.
/// </summary>
public class RotationJournalConformanceTests : IDisposable
{
    private static readonly JsonElement Vector = Fixtures.Json("rotation_journal.json");

    private readonly string _root = Path.Combine(
        Path.GetTempPath(), "strata-rotation-" + Guid.NewGuid().ToString("N"));

    public RotationJournalConformanceTests() => Directory.CreateDirectory(_root);

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }

        GC.SuppressFinalize(this);
    }

    [Fact]
    public void Aad_matches_the_python_constant()
        => Assert.Equal(Vector.Hex("aad_hex"), RotationJournal.Aad.ToArray());

    [Fact]
    public void The_python_journal_unwraps_under_the_old_key()
    {
        WriteFixtureJournal();

        var state = new RotationJournal(_root).Load(Vector.Hex("old_key_hex"));

        Assert.Equal(Vector.Hex("new_key_hex"), state.NewKey);
        Assert.Equal("layer-kat-0001", state.LayerId);
        Assert.Equal("0123456789abcdef0123456789abcdef", state.ManifestObjectId);
        Assert.True(state.PaddingEnabled);
        Assert.Empty(state.DoneObjectIds);
    }

    [Fact]
    public void A_wrong_old_key_cannot_unwrap_the_new_key()
    {
        WriteFixtureJournal();

        Assert.Throws<DecryptionException>(
            () => new RotationJournal(_root).Load(Vector.Hex("new_key_hex")));
    }

    [Fact]
    public void Save_produces_a_journal_the_loader_accepts()
    {
        var journal = new RotationJournal(_root);
        var oldKey = Vector.Hex("old_key_hex");
        var newKey = Vector.Hex("new_key_hex");

        Assert.False(journal.Exists());
        journal.Save("layer-x", "ab".PadRight(32, 'c'), paddingEnabled: false, ["one", "two"], oldKey, newKey);
        Assert.True(journal.Exists());

        var state = journal.Load(oldKey);
        Assert.Equal(newKey, state.NewKey);
        Assert.Equal("layer-x", state.LayerId);
        Assert.False(state.PaddingEnabled);
        Assert.Equal(["one", "two"], state.DoneObjectIds);

        journal.Clear();
        Assert.False(journal.Exists());
    }

    [Fact]
    public void Each_save_uses_a_fresh_nonce()
    {
        var journal = new RotationJournal(_root);
        var oldKey = Vector.Hex("old_key_hex");
        var newKey = Vector.Hex("new_key_hex");

        journal.Save("layer-x", "id", true, [], oldKey, newKey);
        var first = File.ReadAllText(journal.Path);
        journal.Save("layer-x", "id", true, [], oldKey, newKey);

        Assert.NotEqual(first, File.ReadAllText(journal.Path));
    }

    [Fact]
    public void Clear_removes_a_stranded_temporary_file()
    {
        var journal = new RotationJournal(_root);
        var temporary = journal.Path + ".tmp";
        File.WriteAllText(temporary, "{}");

        journal.Clear();

        Assert.False(File.Exists(temporary));
    }

    private void WriteFixtureJournal()
        => File.WriteAllText(
            Path.Combine(_root, RotationJournal.JournalName),
            JsonNode.Parse(Vector.GetProperty("journal").GetRawText())!.ToJsonString());
}
