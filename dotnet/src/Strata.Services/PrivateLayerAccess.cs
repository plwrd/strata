using System.Text.Json.Nodes;
using Strata.Core.Errors;
using Strata.Core.Notes;
using Strata.Infrastructure.Encryption;
using Strata.Infrastructure.Storage;

namespace Strata.Services;

/// <summary>
/// A working handle on an unlocked private layer — port of
/// <c>app/services/private_layer_access.py</c>.
/// </summary>
/// <remarks>
/// <para>
/// Bundles the store, the key, the header and the manifest so callers do not
/// juggle four objects — and, more importantly, so that <em>obtaining</em> one is
/// the single moment where the lock is checked. If you hold a
/// <see cref="PrivateLayerAccess"/>, the layer was unlocked when you got it.
/// </para>
/// <para>
/// Every mutation writes the object and then the manifest. The manifest is written
/// last, so a crash between the two leaves an orphaned ciphertext blob that nothing
/// references — wasted bytes, but never a manifest pointing at an object that does
/// not exist. Losing the reverse would look like data loss.
/// </para>
/// </remarks>
public sealed class PrivateLayerAccess
{
    private const string KindNote = "note";
    private const string KindFolder = "folder";

    private readonly byte[] _key;
    private readonly LayerHeader _header;
    private readonly EncryptedLayerStore _store;
    private Manifest? _manifest;

    public PrivateLayerAccess(string layerId, string root, byte[] key, LayerHeader header)
    {
        LayerId = layerId;
        Root = root;
        _key = key;
        _header = header;
        _store = new EncryptedLayerStore(layerId, root, header.PaddingEnabled);
    }

    public string LayerId { get; }

    public string Root { get; }

    public Manifest Manifest =>
        _manifest ??= _store.ReadManifest(_key, _header.ManifestObjectId);

    private static string Now() => MarkdownLayerStore.NowIso();

    private void Commit() => _store.WriteManifest(_key, _header.ManifestObjectId, Manifest);

    private List<ManifestEntry> Live(string? kind = null)
        => Manifest.Entries.Values
            .Where(entry => entry.TrashedAt is null && (kind is null || entry.Kind == kind))
            .ToList();

    // -- reading -------------------------------------------------------------

    public List<Note> ListNotes()
        => Live(KindNote).Select(entry => _store.ReadNote(_key, entry)).ToList();

    public List<FolderNode> ListFolders()
        => Live(KindFolder)
            .Select(entry => new FolderNode(entry.ObjectId, LayerId, entry.Title, entry.FolderPath, entry.ParentId))
            .ToList();

    public Note GetNote(string noteId)
    {
        var entry = Manifest.Entries.GetValueOrDefault(noteId);
        if (entry is null || entry.Kind != KindNote || entry.TrashedAt is not null)
        {
            throw new NotFoundException("Knowledge object not found.");
        }

        return _store.ReadNote(_key, entry);
    }

    public bool HasNote(string noteId)
    {
        var entry = Manifest.Entries.GetValueOrDefault(noteId);
        return entry is not null && entry.Kind == KindNote && entry.TrashedAt is null;
    }

    private bool TitleTaken(string folderPath, string title, string? ignoring = null)
        => Live(KindNote).Any(entry =>
            entry.ObjectId != ignoring
            && entry.FolderPath == folderPath
            && string.Equals(entry.Title, title, StringComparison.OrdinalIgnoreCase));

    // -- writing -------------------------------------------------------------

    public Note CreateNote(string folderPath, string title, string content, JsonObject? properties = null)
    {
        title = Paths.SafeFilename(title);
        if (TitleTaken(folderPath, title))
        {
            throw new ConflictException("A note with that name already exists in this folder.");
        }

        var entry = _store.WriteNote(
            _key, Manifest, null, title, folderPath, content, properties?.DeepClone().AsObject() ?? [], Now());
        Commit();
        return _store.ReadNote(_key, entry);
    }

    public Note UpdateNote(string noteId, string content)
    {
        var entry = Require(noteId, KindNote);
        var updated = _store.WriteNote(
            _key, Manifest, entry.ObjectId, entry.Title, entry.FolderPath, content, entry.Properties, Now());
        Commit();
        return _store.ReadNote(_key, updated);
    }

    public Note UpdateProperties(string noteId, JsonObject properties)
    {
        var entry = Require(noteId, KindNote);
        var note = _store.ReadNote(_key, entry);
        var updated = _store.WriteNote(
            _key, Manifest, entry.ObjectId, entry.Title, entry.FolderPath, note.Content,
            properties.DeepClone().AsObject(), Now());
        Commit();
        return _store.ReadNote(_key, updated);
    }

    public Note RenameNote(string noteId, string title)
    {
        var entry = Require(noteId, KindNote);
        title = Paths.SafeFilename(title);
        if (TitleTaken(entry.FolderPath, title, ignoring: noteId))
        {
            throw new ConflictException("A note with that name already exists in this folder.");
        }

        // The object id does not change on rename — it is random and has nothing to
        // do with the name. So links by id survive, and the ciphertext is untouched.
        entry.Title = title;
        entry.Filename = $"{title}.md";
        entry.UpdatedAt = Now();
        Commit();
        return _store.ReadNote(_key, entry);
    }

    public Note MoveNote(string noteId, string folderPath)
    {
        var entry = Require(noteId, KindNote);
        if (TitleTaken(folderPath, entry.Title, ignoring: noteId))
        {
            throw new ConflictException("A note with that name already exists in the destination folder.");
        }

        entry.FolderPath = folderPath;
        entry.UpdatedAt = Now();
        Commit();
        return _store.ReadNote(_key, entry);
    }

    public Note DuplicateNote(string noteId)
    {
        var entry = Require(noteId, KindNote);
        var note = _store.ReadNote(_key, entry);

        var baseTitle = $"{entry.Title} copy";
        var title = baseTitle;
        var counter = 2;
        while (TitleTaken(entry.FolderPath, title))
        {
            title = $"{baseTitle} {counter}";
            counter++;
        }

        return CreateNote(entry.FolderPath, title, note.Content, entry.Properties);
    }

    // -- trash ---------------------------------------------------------------

    /// <summary>
    /// Soft-delete. The ciphertext stays encrypted in the layer, not in a plaintext
    /// trash folder — deleting a private note must not decrypt it.
    /// </summary>
    public string TrashNote(string noteId)
    {
        var entry = Require(noteId, KindNote);
        entry.TrashedAt = Now();
        Commit();
        return entry.ObjectId;
    }

    public List<ManifestEntry> ListTrash()
        => Manifest.Entries.Values
            .Where(entry => entry.TrashedAt is not null && entry.Kind == KindNote)
            .ToList();

    public Note RestoreNote(string noteId)
    {
        var entry = Manifest.Entries.GetValueOrDefault(noteId);
        if (entry is null || entry.TrashedAt is null)
        {
            throw new NotFoundException("That trash entry no longer exists.");
        }

        if (TitleTaken(entry.FolderPath, entry.Title))
        {
            throw new ConflictException("A note with that name exists again; rename it first.");
        }

        entry.TrashedAt = null;
        entry.UpdatedAt = Now();
        Commit();
        return _store.ReadNote(_key, entry);
    }

    public int EmptyTrash()
    {
        var removed = 0;
        foreach (var entry in Manifest.Entries.Values.ToList())
        {
            if (entry.TrashedAt is null)
            {
                continue;
            }

            _store.DeleteNoteObject(entry.ObjectId);
            Manifest.Entries.Remove(entry.ObjectId);
            removed++;
        }

        if (removed > 0)
        {
            Commit();
        }

        return removed;
    }

    // -- folders and attachments ---------------------------------------------

    public FolderNode CreateFolder(string folderPath, string name)
    {
        name = Paths.SafeFilename(name);
        var path = folderPath.Length > 0 ? $"{folderPath}/{name}" : name;
        var entry = _store.AddFolder(Manifest, path, name, Now());
        Commit();
        return new FolderNode(entry.ObjectId, LayerId, name, path);
    }

    public FolderNode RenameFolder(string folderId, string name)
    {
        var entry = Require(folderId, KindFolder);
        name = Paths.SafeFilename(name);
        var oldPath = entry.FolderPath;
        var parent = oldPath.Contains('/', StringComparison.Ordinal)
            ? oldPath[..oldPath.LastIndexOf('/')]
            : string.Empty;
        var newPath = parent.Length > 0 ? $"{parent}/{name}" : name;

        entry.Title = name;
        entry.FolderPath = newPath;
        entry.UpdatedAt = Now();

        Reparent(folderId, oldPath, newPath);
        Commit();
        return new FolderNode(folderId, LayerId, name, newPath);
    }

    /// <summary>
    /// Reparent a folder under another folder (or the layer root). Same-layer only;
    /// refuses moves into itself or a descendant.
    /// </summary>
    public FolderNode MoveFolder(string folderId, string parentFolderPath)
    {
        var entry = Require(folderId, KindFolder);
        var oldPath = entry.FolderPath;
        var name = entry.Title;
        var parent = parentFolderPath.Trim().Trim('/');

        if (parent == oldPath || parent.StartsWith($"{oldPath}/", StringComparison.Ordinal))
        {
            throw new InvalidRequestException("A folder cannot be moved into itself.");
        }

        if (parent.Length > 0
            && !Manifest.Entries.Values.Any(other => other.Kind == KindFolder && other.FolderPath == parent))
        {
            throw new NotFoundException("Destination folder not found.");
        }

        var newPath = parent.Length > 0 ? $"{parent}/{name}" : name;
        if (newPath == oldPath)
        {
            return new FolderNode(folderId, LayerId, name, oldPath);
        }

        if (Manifest.Entries.Values.Any(other =>
                other.Kind == KindFolder && other.FolderPath == newPath && other.ObjectId != folderId))
        {
            throw new ConflictException("A folder with that name already exists.");
        }

        entry.FolderPath = newPath;
        entry.UpdatedAt = Now();

        Reparent(folderId, oldPath, newPath);
        Commit();
        return new FolderNode(folderId, LayerId, name, newPath);
    }

    /// <summary>Every note and subfolder beneath a moved folder moves with it.</summary>
    private void Reparent(string folderId, string oldPath, string newPath)
    {
        foreach (var other in Manifest.Entries.Values)
        {
            if (other.ObjectId == folderId)
            {
                continue;
            }

            if (other.FolderPath == oldPath)
            {
                other.FolderPath = newPath;
            }
            else if (other.FolderPath.StartsWith($"{oldPath}/", StringComparison.Ordinal))
            {
                other.FolderPath = newPath + other.FolderPath[oldPath.Length..];
            }
        }
    }

    public int DeleteFolder(string folderId)
    {
        var entry = Require(folderId, KindFolder);
        var path = entry.FolderPath;
        var trashed = 0;
        var timestamp = Now();

        foreach (var other in Manifest.Entries.Values)
        {
            if (other.Kind != KindNote || other.TrashedAt is not null)
            {
                continue;
            }

            if (other.FolderPath == path || other.FolderPath.StartsWith($"{path}/", StringComparison.Ordinal))
            {
                other.TrashedAt = timestamp;
                trashed++;
            }
        }

        Manifest.Entries.Remove(folderId);
        Commit();
        return trashed;
    }

    public string SaveAttachment(string filename, ReadOnlySpan<byte> data)
    {
        var entry = _store.WriteAttachment(_key, Manifest, filename, data, Now());
        Commit();

        // The "path" of a private attachment is its opaque object id: there is no
        // folder on disk to point at, and inventing a readable one would leak.
        return $"strata-object://{entry.ObjectId}";
    }

    public byte[] ReadAttachment(string objectId) => _store.ReadAttachment(_key, objectId);

    // -- helpers -------------------------------------------------------------

    private ManifestEntry Require(string objectId, string kind)
    {
        var entry = Manifest.Entries.GetValueOrDefault(objectId);
        if (entry is null || entry.Kind != kind)
        {
            throw new NotFoundException("Knowledge object not found.");
        }

        return entry;
    }
}
