using Strata.Core.Errors;
using Strata.Core.Layers;
using Strata.Core.Notes;

namespace Strata.Services;

/// <summary>
/// The candidate set a view or a search runs over.
/// </summary>
/// <remarks>
/// A seam, not an abstraction for its own sake: <see cref="ViewService"/> needs
/// notes from readable layers and nothing else, so it should not depend on the
/// whole note service.
/// </remarks>
public interface INoteSource
{
    /// <summary>Notes from readable layers, optionally narrowed to <paramref name="layerIds"/>.</summary>
    List<Note> ListNotes(IReadOnlyList<string>? layerIds = null);

    List<FolderNode> ListFolders(IReadOnlyList<string>? layerIds = null);
}

/// <summary>
/// The read half of <c>app/services/note_service.py</c>: listing and locating notes
/// across both storage kinds.
/// </summary>
/// <remarks>
/// <para>
/// The mutation half (create/update/rename/move/trash/links) is <b>not ported
/// yet</b>; this is deliberately named for what it does rather than claiming to be
/// the whole service.
/// </para>
/// <para>
/// Only readable layers contribute. <see cref="WorkspaceService.ReadableLayers"/>
/// excludes locked ones by asking the key holder, not the descriptor, so a locked
/// layer cannot leak a title here by construction rather than by a filter someone
/// might forget.
/// </para>
/// </remarks>
public sealed class NoteReader(WorkspaceService workspace) : INoteSource
{
    private List<string> LayersWithStorage(LayerStorage storage, IReadOnlyList<string>? layerIds)
        => workspace.ReadableLayers()
            .Where(layer => layer.Storage == storage && (layerIds is null || layerIds.Contains(layer.Id)))
            .Select(layer => layer.Id)
            .ToList();

    public List<Note> ListNotes(IReadOnlyList<string>? layerIds = null)
    {
        var notes = new List<Note>();
        foreach (var layerId in LayersWithStorage(LayerStorage.Markdown, layerIds))
        {
            notes.AddRange(workspace.LayerStore(layerId).ListNotes());
        }

        foreach (var layerId in LayersWithStorage(LayerStorage.EncryptedObjects, layerIds))
        {
            notes.AddRange(workspace.PrivateAccess(layerId).ListNotes());
        }

        return notes;
    }

    public List<FolderNode> ListFolders(IReadOnlyList<string>? layerIds = null)
    {
        var folders = new List<FolderNode>();
        foreach (var layerId in LayersWithStorage(LayerStorage.Markdown, layerIds))
        {
            folders.AddRange(workspace.LayerStore(layerId).ListFolders());
        }

        foreach (var layerId in LayersWithStorage(LayerStorage.EncryptedObjects, layerIds))
        {
            folders.AddRange(workspace.PrivateAccess(layerId).ListFolders());
        }

        return folders;
    }

    public Note GetNote(string noteId)
    {
        foreach (var layerId in LayersWithStorage(LayerStorage.Markdown, null))
        {
            if (workspace.LayerStore(layerId).Locate(noteId) is { } located)
            {
                return located.Note;
            }
        }

        foreach (var layerId in LayersWithStorage(LayerStorage.EncryptedObjects, null))
        {
            var access = workspace.PrivateAccess(layerId);
            if (access.HasNote(noteId))
            {
                return access.GetNote(noteId);
            }
        }

        throw new NotFoundException("Knowledge object not found.");
    }
}
