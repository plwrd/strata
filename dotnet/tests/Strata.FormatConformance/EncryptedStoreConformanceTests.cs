using System.Text.Json;
using System.Text.Json.Nodes;
using Strata.Core.Errors;
using Strata.Core.Notes;
using Strata.Infrastructure.Encryption;
using Strata.Infrastructure.Storage;

namespace Strata.FormatConformance;

/// <summary>
/// End-to-end interop: open a private layer the <em>Python</em> app wrote, using
/// nothing but the password. This is the test that says a user's existing
/// workspace survives the migration.
/// </summary>
public class EncryptedStoreConformanceTests
{
    private static readonly JsonElement Vector = Fixtures.Json("store_layer.json");

    private static string LayerRoot =>
        Path.Combine(Fixtures.Root, Vector.GetProperty("directory").GetString()!);

    private static string LayerId => Vector.GetProperty("layer_id").GetString()!;

    private static EncryptedLayerStore Store() => new(LayerId, LayerRoot);

    private static byte[] UnlockWithPassword()
        => LayerHeader.Load(LayerRoot).UnlockWithPassword(Vector.GetProperty("password").GetString()!);

    [Fact]
    public void The_password_unwraps_the_layer_key_from_the_python_header()
        => Assert.Equal(Vector.Hex("layer_key_hex"), UnlockWithPassword());

    [Fact]
    public void The_header_points_at_the_manifest()
        => Assert.Equal(
            Vector.GetProperty("manifest_object_id").GetString(),
            LayerHeader.Load(LayerRoot).ManifestObjectId);

    [Fact]
    public void Object_ids_on_disk_match_what_python_wrote()
    {
        var expected = Vector.GetProperty("object_ids").EnumerateArray()
            .Select(id => id.GetString()!).ToList();

        Assert.Equal(expected, Store().ObjectIds());
    }

    [Fact]
    public void Objects_are_sharded_by_the_first_two_hex_characters()
    {
        foreach (var objectId in Store().ObjectIds())
        {
            var path = Path.Combine(LayerRoot, EncryptedLayerStore.ObjectsDir, objectId[..2], objectId);
            Assert.True(File.Exists(path), $"Expected {objectId} under shard {objectId[..2]}");
        }
    }

    [Fact]
    public void Nothing_on_disk_leaks_a_title()
    {
        // Opaque ids only: no filenames, no extensions, no folders that mean anything.
        var title = Vector.GetProperty("note").GetProperty("title").GetString()!;
        foreach (var path in Directory.EnumerateFiles(LayerRoot, "*", SearchOption.AllDirectories))
        {
            Assert.DoesNotContain(title, Path.GetFileName(path), StringComparison.OrdinalIgnoreCase);
        }
    }

    [Fact]
    public void The_manifest_lists_every_entry_python_recorded()
    {
        var manifest = Store().ReadManifest(UnlockWithPassword(), Vector.GetProperty("manifest_object_id").GetString()!);

        Assert.Equal(Manifest.FormatVersionValue, manifest.FormatVersion);
        Assert.Equal(4, manifest.Entries.Count); // two notes, one folder, one attachment
        Assert.Contains(Vector.GetProperty("note").GetProperty("object_id").GetString(), manifest.Entries.Keys);
        Assert.Contains(Vector.GetProperty("folder").GetProperty("object_id").GetString(), manifest.Entries.Keys);
        Assert.Contains(Vector.GetProperty("attachment").GetProperty("object_id").GetString(), manifest.Entries.Keys);
    }

    [Fact]
    public void A_note_written_by_python_reads_back_with_its_body_and_parsed_metadata()
    {
        var expected = Vector.GetProperty("note");
        var key = UnlockWithPassword();
        var store = Store();
        var manifest = store.ReadManifest(key, Vector.GetProperty("manifest_object_id").GetString()!);
        var entry = manifest.Entries[expected.GetProperty("object_id").GetString()!];

        var note = store.ReadNote(key, entry);

        Assert.Equal(expected.GetProperty("content").GetString(), note.Content);
        Assert.Equal(expected.GetProperty("title").GetString(), note.Metadata.Title);
        Assert.Equal(LayerId, note.Metadata.LayerId);
        Assert.Equal(expected.GetProperty("word_count").GetInt32(), note.Metadata.WordCount);
        Assert.Equal(expected.GetProperty("size_bytes").GetInt32(), note.Metadata.SizeBytes);
        Assert.Equal("open", note.Metadata.Properties["status"]!.GetValue<string>());

        Assert.Equal(
            expected.GetProperty("expected_tags").EnumerateArray().Select(tag => tag.GetString()!).ToList(),
            note.Metadata.Tags);

        Assert.Equal(
            expected.GetProperty("expected_link_targets").EnumerateArray().Select(t => t.GetString()!).ToList(),
            note.Metadata.Links.Select(link => link.TargetTitle).ToList());

        foreach (var typed in expected.GetProperty("expected_typed_relationship").EnumerateObject())
        {
            var link = Assert.Single(note.Metadata.Links, l => l.TargetTitle == typed.Name);
            Assert.Equal(typed.Value.GetString(), link.Relationship);
        }
    }

    [Fact]
    public void A_note_in_a_folder_keeps_its_display_path()
    {
        var expected = Vector.GetProperty("second_note");
        var key = UnlockWithPassword();
        var store = Store();
        var manifest = store.ReadManifest(key, Vector.GetProperty("manifest_object_id").GetString()!);

        var note = store.ReadNote(key, manifest.Entries[expected.GetProperty("object_id").GetString()!]);

        Assert.Equal(expected.GetProperty("content").GetString(), note.Content);
        Assert.Equal(expected.GetProperty("folder_path").GetString(), note.Metadata.FolderPath);
        Assert.Equal("Deals/Second Note.md", note.Metadata.DisplayPath);
        Assert.Empty(note.Metadata.Links);
    }

    [Fact]
    public void An_attachment_written_by_python_reads_back_byte_for_byte()
    {
        var expected = Vector.GetProperty("attachment");
        var data = Store().ReadAttachment(UnlockWithPassword(), expected.GetProperty("object_id").GetString()!);

        Assert.Equal(expected.Hex("data_hex"), data);
    }

    [Fact]
    public void A_folder_has_no_object_file_of_its_own()
    {
        var folderId = Vector.GetProperty("folder").GetProperty("object_id").GetString()!;
        Assert.DoesNotContain(folderId, Store().ObjectIds());
    }

    [Fact]
    public void The_wrong_password_opens_nothing()
        => Assert.Throws<DecryptionException>(() => LayerHeader.Load(LayerRoot).UnlockWithPassword("wrong"));

    [Fact]
    public void An_object_cannot_be_read_as_the_wrong_type()
    {
        var key = UnlockWithPassword();
        var noteId = Vector.GetProperty("note").GetProperty("object_id").GetString()!;

        Assert.Throws<DecryptionException>(() => Store().ReadAttachment(key, noteId));
    }

    [Fact]
    public void A_missing_object_is_reported_as_not_found()
        => Assert.Throws<NotFoundException>(
            () => Store().ReadAttachment(UnlockWithPassword(), new string('f', 32)));
}

/// <summary>
/// Round-trip behaviour of the store itself, in a scratch layer.
/// </summary>
public class EncryptedStoreRoundTripTests : IDisposable
{
    private const string Timestamp = "2020-01-01T00:00:00+00:00";

    private readonly string _root = Path.Combine(
        Path.GetTempPath(), "strata-store-" + Guid.NewGuid().ToString("N"));

    private readonly byte[] _key = AeadPrimitives.RandomKey();

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }

        GC.SuppressFinalize(this);
    }

    private EncryptedLayerStore Store(bool padding = true)
    {
        var store = new EncryptedLayerStore("layer-scratch", _root, padding);
        store.Ensure();
        return store;
    }

    [Fact]
    public void Notes_folders_and_attachments_survive_a_write_read_cycle()
    {
        var store = Store();
        var manifestId = store.CreateManifest(_key);
        var manifest = store.ReadManifest(_key, manifestId);

        var note = store.WriteNote(
            _key, manifest, null, "Title", "Folder", "Body with #tag and [[Link]].", [], Timestamp);
        store.AddFolder(manifest, "Folder", "Folder", Timestamp);
        var attachment = store.WriteAttachment(_key, manifest, "a.bin", [1, 2, 3], Timestamp);
        store.WriteManifest(_key, manifestId, manifest);

        var reopened = store.ReadManifest(_key, manifestId);
        Assert.Equal(3, reopened.Entries.Count);
        Assert.Equal("Body with #tag and [[Link]].", store.ReadNote(_key, reopened.Entries[note.ObjectId]).Content);
        Assert.Equal([1, 2, 3], store.ReadAttachment(_key, attachment.ObjectId));
    }

    [Fact]
    public void Rewriting_a_note_keeps_its_created_at_and_aliases()
    {
        var store = Store();
        var manifest = new Manifest();
        var first = store.WriteNote(_key, manifest, null, "T", "", "one", [], "2020-01-01T00:00:00+00:00");
        first.Aliases.Add("Nickname");

        var second = store.WriteNote(
            _key, manifest, first.ObjectId, "T", "", "two", [], "2021-06-06T00:00:00+00:00");

        Assert.Equal(first.ObjectId, second.ObjectId);
        Assert.Equal("2020-01-01T00:00:00+00:00", second.CreatedAt);
        Assert.Equal("2021-06-06T00:00:00+00:00", second.UpdatedAt);
        Assert.Equal(["Nickname"], second.Aliases);
        Assert.Single(manifest.Entries);
    }

    [Fact]
    public void Tags_in_properties_are_copied_onto_the_entry()
    {
        var store = Store();
        var manifest = new Manifest();
        var properties = new JsonObject { ["tags"] = new JsonArray("alpha", "beta") };

        var entry = store.WriteNote(_key, manifest, null, "T", "", "body", properties, Timestamp);

        Assert.Equal(["alpha", "beta"], entry.Tags);
    }

    [Fact]
    public void A_duplicate_folder_path_is_a_conflict()
    {
        var manifest = new Manifest();
        Store().AddFolder(manifest, "Deals", "Deals", Timestamp);

        Assert.Throws<ConflictException>(() => Store().AddFolder(manifest, "Deals", "Deals", Timestamp));
    }

    [Fact]
    public void Deleting_a_note_object_removes_only_the_ciphertext()
    {
        var store = Store();
        var manifest = new Manifest();
        var entry = store.WriteNote(_key, manifest, null, "T", "", "body", [], Timestamp);

        store.DeleteNoteObject(entry.ObjectId);

        Assert.DoesNotContain(entry.ObjectId, store.ObjectIds());
        Assert.Contains(entry.ObjectId, manifest.Entries.Keys); // the trash still knows about it
    }

    [Fact]
    public void ObjectIds_ignores_stranded_temporary_files()
    {
        var store = Store();
        var manifest = new Manifest();
        var entry = store.WriteNote(_key, manifest, null, "T", "", "body", [], Timestamp);
        var shard = Path.Combine(_root, EncryptedLayerStore.ObjectsDir, entry.ObjectId[..2]);
        File.WriteAllText(Path.Combine(shard, "deadbeef.tmp"), "half-written");

        Assert.Equal([entry.ObjectId], store.ObjectIds());
    }

    [Fact]
    public void Rotation_rewrites_every_object_under_the_new_key()
    {
        var store = Store();
        var manifestId = store.CreateManifest(_key);
        var manifest = store.ReadManifest(_key, manifestId);
        var note = store.WriteNote(_key, manifest, null, "T", "", "secret body", [], Timestamp);
        var attachment = store.WriteAttachment(_key, manifest, "a.bin", [9, 9], Timestamp);
        store.AddFolder(manifest, "F", "F", Timestamp);
        store.WriteManifest(_key, manifestId, manifest);

        var newKey = AeadPrimitives.RandomKey();
        var progress = new List<string>();
        var rewritten = store.Rotate(_key, newKey, manifestId, done: null, onProgress: progress.Add);

        // Two objects plus the manifest; the folder lives only in the manifest.
        Assert.Equal(3, rewritten);
        Assert.Equal(3, progress.Count);

        var rotated = store.ReadManifest(newKey, manifestId);
        Assert.Equal("secret body", store.ReadNote(newKey, rotated.Entries[note.ObjectId]).Content);
        Assert.Equal([9, 9], store.ReadAttachment(newKey, attachment.ObjectId));

        // The old key is what rotation exists to revoke.
        Assert.Throws<DecryptionException>(() => store.ReadManifest(_key, manifestId));
    }

    [Fact]
    public void Rotation_resumes_after_a_crash_from_the_journalled_set()
    {
        var store = Store();
        var manifestId = store.CreateManifest(_key);
        var manifest = store.ReadManifest(_key, manifestId);
        var first = store.WriteNote(_key, manifest, null, "A", "", "alpha", [], Timestamp);
        var second = store.WriteNote(_key, manifest, null, "B", "", "beta", [], Timestamp);
        store.WriteManifest(_key, manifestId, manifest);

        var newKey = AeadPrimitives.RandomKey();
        var journalled = new HashSet<string>();

        // Crash after the first object is rewritten and recorded.
        Assert.Throws<IOException>(() => store.Rotate(_key, newKey, manifestId, done: null, onProgress: id =>
        {
            journalled.Add(id);
            throw new IOException("power cut");
        }));
        Assert.Single(journalled);

        // Mid-flight the layer is genuinely mixed: one object under each key.
        Assert.Equal("beta", store.ReadNote(_key, manifest.Entries[second.ObjectId]).Content);

        var resumed = store.Rotate(_key, newKey, manifestId, done: journalled);

        Assert.Equal(3, resumed);
        var rotated = store.ReadManifest(newKey, manifestId);
        Assert.Equal("alpha", store.ReadNote(newKey, rotated.Entries[first.ObjectId]).Content);
        Assert.Equal("beta", store.ReadNote(newKey, rotated.Entries[second.ObjectId]).Content);
    }

    [Fact]
    public void Rotation_recovers_an_object_that_was_rewritten_but_never_journalled()
    {
        var store = Store();
        var manifestId = store.CreateManifest(_key);
        var manifest = store.ReadManifest(_key, manifestId);
        var note = store.WriteNote(_key, manifest, null, "A", "", "alpha", [], Timestamp);
        store.WriteManifest(_key, manifestId, manifest);

        var newKey = AeadPrimitives.RandomKey();

        // The write landed, the journal entry did not: the crash window between them.
        Assert.Throws<IOException>(() => store.Rotate(_key, newKey, manifestId, done: null, onProgress: _
            => throw new IOException("power cut")));

        // Resuming with an empty done-set still finds the object, via the old-key-fails
        // fallback rather than by guessing.
        Assert.Equal(2, store.Rotate(_key, newKey, manifestId, done: null));
        var rotated = store.ReadManifest(newKey, manifestId);
        Assert.Equal("alpha", store.ReadNote(newKey, rotated.Entries[note.ObjectId]).Content);
    }

    [Fact]
    public void Padding_can_be_turned_off_per_layer()
    {
        var store = Store(padding: false);
        var manifest = new Manifest();
        var entry = store.WriteNote(_key, manifest, null, "T", "", "tiny", [], Timestamp);

        var blob = File.ReadAllBytes(
            Path.Combine(_root, EncryptedLayerStore.ObjectsDir, entry.ObjectId[..2], entry.ObjectId));

        Assert.Equal(0, ObjectHeader.Unpack(blob).Flags & ObjectContainer.FlagPadded);
        Assert.Equal("tiny", store.ReadNote(_key, entry).Content);
    }

    [Fact]
    public void Object_ids_are_random_not_derived_from_the_title()
    {
        var manifest = new Manifest();
        var store = Store();

        var first = store.WriteNote(_key, manifest, null, "Same Title", "", "one", [], Timestamp);
        var second = store.WriteNote(_key, manifest, null, "Same Title", "", "two", [], Timestamp);

        Assert.NotEqual(first.ObjectId, second.ObjectId);
    }
}
