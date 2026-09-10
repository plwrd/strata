using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Strata.Core.Errors;
using Strata.Core.Notes;
using Strata.Infrastructure.Encryption;

namespace Strata.Infrastructure.Storage;

/// <summary>
/// Encrypted object storage for private layers — port of
/// <c>app/infrastructure/storage/encrypted_store.py</c>.
/// </summary>
/// <remarks>
/// <para>On disk:</para>
/// <code>
/// layers/layer_ab12/
///   layer.header                 # wrapped keys only; no content, no names
///   objects/
///     02/02f8a72be4304c92…       # opaque: 16 random bytes as 32 hex chars
///     a9/a937c89dc81f4cc8…
/// </code>
/// <para>
/// There are no filenames, no extensions, and no folders that mean anything.
/// Object ids are <b>random</b>, never derived from the name (ADR-0004):
/// deterministic filename encryption would let anyone with the directory listing
/// confirm a guess — "does this vault contain a note called
/// <c>Acquisition of Northwind</c>?" would become a single hash comparison.
/// </para>
/// <para>
/// The layer key is passed in on every call rather than held: this object cannot be
/// used after a lock, because the caller has nothing to pass it.
/// </para>
/// </remarks>
public sealed class EncryptedLayerStore(string layerId, string root, bool padding = true)
{
    public const string ObjectsDir = "objects";

    public string LayerId { get; } = layerId;

    public string Root { get; } = root;

    public bool Padding { get; } = padding;

    public static string NewRawObjectId()
        => Convert.ToHexString(RandomNumberGenerator.GetBytes(ObjectContainer.ObjectIdBytes)).ToLowerInvariant();

    // -- object files --------------------------------------------------------

    public void Ensure() => Directory.CreateDirectory(Path.Combine(Root, ObjectsDir));

    private string ObjectPath(string objectId)
        => Path.Combine(Root, ObjectsDir, objectId[..2], objectId);

    private void WriteObject(ReadOnlySpan<byte> key, string objectId, byte objectType, ReadOnlySpan<byte> plaintext)
    {
        var blob = ObjectContainer.Seal(
            key, LayerId, Convert.FromHexString(objectId), objectType, plaintext, pad: Padding);

        var path = ObjectPath(objectId);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporary = path + ".tmp";
        try
        {
            File.WriteAllBytes(temporary, blob);
            AtomicFile.Replace(temporary, path);
        }
        catch
        {
            AtomicFile.TryDelete(temporary);
            throw;
        }
    }

    private byte[] ReadObject(ReadOnlySpan<byte> key, string objectId, byte objectType)
    {
        var path = ObjectPath(objectId);
        if (!File.Exists(path))
        {
            throw new NotFoundException("Knowledge object not found.");
        }

        return ObjectContainer.OpenSealed(
            key, LayerId, Convert.FromHexString(objectId), objectType, File.ReadAllBytes(path));
    }

    private void DeleteObject(string objectId)
    {
        var path = ObjectPath(objectId);
        if (File.Exists(path))
        {
            File.Delete(path);
        }
    }

    public List<string> ObjectIds()
    {
        var objects = Path.Combine(Root, ObjectsDir);
        if (!Directory.Exists(objects))
        {
            return [];
        }

        return Directory.EnumerateDirectories(objects)
            .SelectMany(Directory.EnumerateFiles)
            .Select(Path.GetFileName)
            .Where(name => name is not null && !name.EndsWith(".tmp", StringComparison.Ordinal))
            .Select(name => name!)
            .Order(StringComparer.Ordinal)
            .ToList();
    }

    // -- manifest ------------------------------------------------------------

    public string CreateManifest(ReadOnlySpan<byte> key)
    {
        var objectId = NewRawObjectId();
        WriteObject(key, objectId, ObjectTypes.Manifest, new Manifest().ToBytes());
        return objectId;
    }

    public Manifest ReadManifest(ReadOnlySpan<byte> key, string manifestId)
        => Manifest.FromBytes(ReadObject(key, manifestId, ObjectTypes.Manifest));

    public void WriteManifest(ReadOnlySpan<byte> key, string manifestId, Manifest manifest)
        => WriteObject(key, manifestId, ObjectTypes.Manifest, manifest.ToBytes());

    // -- notes ---------------------------------------------------------------

    public Note ReadNote(ReadOnlySpan<byte> key, ManifestEntry entry)
    {
        // errors="replace": a body that is not valid UTF-8 is damaged content, not a
        // reason to make the whole note unreadable.
        var body = new UTF8Encoding(false, throwOnInvalidBytes: false)
            .GetString(ReadObject(key, entry.ObjectId, ObjectTypes.Note));

        return new Note(MetadataFor(entry, body), body);
    }

    private NoteMetadata MetadataFor(ManifestEntry entry, string body) => new()
    {
        Id = entry.ObjectId,
        LayerId = LayerId,
        Title = entry.Title,
        FolderPath = entry.FolderPath,
        Aliases = [.. entry.Aliases],
        Tags = NoteParsing.ExtractTags(body, entry.Tags),
        Properties = entry.Properties.DeepClone().AsObject(),
        Links = NoteParsing.ExtractLinks(body),
        CreatedAt = entry.CreatedAt,
        UpdatedAt = entry.UpdatedAt,
        SizeBytes = entry.SizeBytes,
        WordCount = entry.WordCount,
    };

    public ManifestEntry WriteNote(
        ReadOnlySpan<byte> key,
        Manifest manifest,
        string? objectId,
        string title,
        string folderPath,
        string content,
        JsonObject properties,
        string timestamp)
    {
        objectId ??= NewRawObjectId();
        var body = Encoding.UTF8.GetBytes(content);
        WriteObject(key, objectId, ObjectTypes.Note, body);

        manifest.Entries.TryGetValue(objectId, out var existing);
        var entry = new ManifestEntry
        {
            ObjectId = objectId,
            Kind = "note",
            Title = title,
            FolderPath = folderPath,
            Filename = $"{title}.md",
            Tags = properties["tags"] is JsonArray tags
                ? tags.Where(tag => tag is JsonValue value && value.TryGetValue<string>(out _))
                    .Select(tag => tag!.GetValue<string>())
                    .ToList()
                : [],
            Aliases = existing is not null ? [.. existing.Aliases] : [],
            Properties = properties.DeepClone().AsObject(),
            CreatedAt = existing?.CreatedAt ?? timestamp,
            UpdatedAt = timestamp,
            SizeBytes = body.Length,
            WordCount = NoteParsing.WordCount(content),
            TrashedAt = existing?.TrashedAt,
        };

        manifest.Entries[objectId] = entry;
        return entry;
    }

    /// <summary>Remove the ciphertext. Only called when emptying the trash.</summary>
    public void DeleteNoteObject(string objectId) => DeleteObject(objectId);

    // -- folders and attachments ---------------------------------------------

    public ManifestEntry AddFolder(Manifest manifest, string path, string name, string timestamp)
    {
        if (manifest.Entries.Values.Any(entry => entry.Kind == "folder" && entry.FolderPath == path))
        {
            throw new ConflictException("A folder with that name already exists.");
        }

        var entry = new ManifestEntry
        {
            ObjectId = NewRawObjectId(),
            Kind = "folder",
            Title = name,
            FolderPath = path,
            CreatedAt = timestamp,
            UpdatedAt = timestamp,
        };

        manifest.Entries[entry.ObjectId] = entry;
        return entry;
    }

    public ManifestEntry WriteAttachment(
        ReadOnlySpan<byte> key,
        Manifest manifest,
        string filename,
        ReadOnlySpan<byte> data,
        string timestamp)
    {
        var objectId = NewRawObjectId();
        WriteObject(key, objectId, ObjectTypes.Attachment, data);

        var entry = new ManifestEntry
        {
            ObjectId = objectId,
            Kind = "attachment",
            Title = filename,
            Filename = filename,
            CreatedAt = timestamp,
            UpdatedAt = timestamp,
            SizeBytes = data.Length,
        };

        manifest.Entries[objectId] = entry;
        return entry;
    }

    public byte[] ReadAttachment(ReadOnlySpan<byte> key, string objectId)
        => ReadObject(key, objectId, ObjectTypes.Attachment);

    // -- rotation ------------------------------------------------------------

    /// <summary>Re-encrypt every object under a new key.</summary>
    /// <remarks>
    /// This is what actually revokes someone who kept the old key. It is expensive
    /// (every object is rewritten), and it is the only honest way to do it.
    /// Object ids are preserved so the manifest stays valid, and each object is
    /// written atomically. <paramref name="done"/> / <paramref name="onProgress"/>
    /// let a rotation journal resume after a crash: already-rewritten objects are
    /// read with the new key, the rest with the old key.
    /// </remarks>
    public int Rotate(
        byte[] oldKey,
        byte[] newKey,
        string manifestId,
        ISet<string>? done = null,
        Action<string>? onProgress = null)
    {
        var completed = new HashSet<string>(done ?? new HashSet<string>());
        var manifest = ReadManifest(completed.Contains(manifestId) ? newKey : oldKey, manifestId);
        var rewritten = 0;

        foreach (var entry in manifest.Entries.Values)
        {
            byte? objectType = entry.Kind switch
            {
                "note" => ObjectTypes.Note,
                "attachment" => ObjectTypes.Attachment,
                _ => null, // folders exist only in the manifest
            };

            if (objectType is null)
            {
                continue;
            }

            var plaintext = ReadForRotation(oldKey, newKey, entry.ObjectId, objectType.Value, completed);
            if (!completed.Contains(entry.ObjectId))
            {
                WriteObject(newKey, entry.ObjectId, objectType.Value, plaintext);
                completed.Add(entry.ObjectId);
                onProgress?.Invoke(entry.ObjectId);
            }

            rewritten += 1;
        }

        if (!completed.Contains(manifestId))
        {
            WriteObject(newKey, manifestId, ObjectTypes.Manifest, manifest.ToBytes());
            completed.Add(manifestId);
            onProgress?.Invoke(manifestId);
        }

        return rewritten + 1;
    }

    private byte[] ReadForRotation(
        byte[] oldKey,
        byte[] newKey,
        string objectId,
        byte objectType,
        HashSet<string> completed)
    {
        if (completed.Contains(objectId))
        {
            return ReadObject(newKey, objectId, objectType);
        }

        try
        {
            return ReadObject(oldKey, objectId, objectType);
        }
        catch (DecryptionException)
        {
            return ReadObject(newKey, objectId, objectType);
        }
    }
}
