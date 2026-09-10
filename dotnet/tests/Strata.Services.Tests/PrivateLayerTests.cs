using System.Text.Json.Nodes;
using Strata.Core.Errors;
using Strata.Core.Layers;
using Strata.Infrastructure.Encryption;

namespace Strata.Services.Tests;

/// <summary>
/// Private layers end to end: create, unlock, lock, remember, rotate — and the
/// lock semantics that decide whether anything can read them.
/// </summary>
public class PrivateLayerTests : IDisposable
{
    private readonly WorkspaceFixture _fixture = new();

    public void Dispose()
    {
        _fixture.Dispose();
        GC.SuppressFinalize(this);
    }

    private (WorkspaceService Workspace, LayerDescriptor Layer, string? Recovery) Private(
        bool withRecoveryKey = true)
    {
        var workspace = _fixture.Created();
        var (layer, recovery) = workspace.CreateLayer(
            "Private", LayerVisibility.Private,
            password: WorkspaceFixture.Password, withRecoveryKey: withRecoveryKey);
        return (workspace, layer, recovery);
    }

    // -- creation ------------------------------------------------------------

    [Fact]
    public void A_new_private_layer_starts_unlocked_and_hands_back_a_recovery_key_once()
    {
        var (workspace, layer, recovery) = Private();

        Assert.Equal(LayerState.Unlocked, layer.State);
        Assert.Equal(LayerStorage.EncryptedObjects, layer.Storage);
        Assert.Equal("layer-private", layer.Color);
        Assert.NotNull(recovery);
        Assert.Equal(13, recovery!.Split('-').Length);
        Assert.True(workspace.Encryption!.IsUnlocked(layer.Id));
    }

    [Fact]
    public void A_private_layer_defaults_to_local_only_ai()
    {
        var (_, layer, _) = Private();

        Assert.Equal(AIAccess.LocalOnly, layer.AiPolicy.Access);
        Assert.Equal(EmbeddingAccess.LocalOnly, layer.AiPolicy.Embeddings);
        Assert.False(layer.AiPolicy.AllowsRemote);
    }

    [Fact]
    public void A_recovery_key_can_be_declined()
        => Assert.Null(Private(withRecoveryKey: false).Recovery);

    [Fact]
    public void A_short_password_is_refused_before_anything_is_written()
    {
        var workspace = _fixture.Created();

        var error = Assert.Throws<InvalidRequestException>(() => workspace.CreateLayer(
            "Private", LayerVisibility.Private, password: "short"));

        Assert.Equal(EncryptionService.MinPasswordLength, error.Details["minimum"]);
        Assert.Single(workspace.Descriptor.Layers); // nothing was appended
    }

    // -- lock semantics ------------------------------------------------------

    [Fact]
    public void Reopening_a_workspace_locks_every_private_layer_whatever_the_file_says()
    {
        var (_, layer, _) = Private();

        // The descriptor on disk says "unlocked"; a fresh process holds no key.
        var reopened = new WorkspaceService(encryption: new EncryptionService());
        reopened.Open(_fixture.Root);

        Assert.Equal(LayerState.Locked, reopened.RequireLayer(layer.Id).State);
        Assert.Throws<LayerLockedException>(() => reopened.RequireReadableLayer(layer.Id));
        Assert.Throws<LayerLockedException>(() => reopened.PrivateAccess(layer.Id));
    }

    [Fact]
    public void A_hand_edited_unlocked_state_does_not_grant_access()
    {
        var (_, layer, _) = Private();

        // Simulate someone flipping the JSON: the key holder is still empty.
        var reopened = new WorkspaceService(encryption: new EncryptionService());
        reopened.Open(_fixture.Root);

        Assert.Throws<LayerLockedException>(() => reopened.RequireReadableLayer(layer.Id));
        Assert.DoesNotContain(layer.Id, reopened.ReadableLayers().Select(l => l.Id));
        Assert.Contains(layer.Id, reopened.LockedLayers().Select(l => l.Id));
    }

    [Fact]
    public void Unlocking_with_the_password_restores_access()
    {
        var (_, layer, _) = Private();
        var reopened = new WorkspaceService(encryption: new EncryptionService());
        reopened.Open(_fixture.Root);

        var unlocked = reopened.UnlockLayer(layer.Id, WorkspaceFixture.Password);

        Assert.Equal(LayerState.Unlocked, unlocked.State);
        Assert.NotNull(reopened.PrivateAccess(layer.Id));
    }

    [Fact]
    public void Unlocking_with_the_recovery_key_restores_access()
    {
        var (_, layer, recovery) = Private();
        var reopened = new WorkspaceService(encryption: new EncryptionService());
        reopened.Open(_fixture.Root);

        reopened.UnlockLayerWithRecoveryKey(layer.Id, recovery!);

        Assert.Equal(LayerState.Unlocked, reopened.RequireLayer(layer.Id).State);
    }

    [Fact]
    public void A_wrong_password_says_nothing_about_why()
    {
        var (_, layer, _) = Private();
        var reopened = new WorkspaceService(encryption: new EncryptionService());
        reopened.Open(_fixture.Root);

        var error = Assert.Throws<DecryptionException>(() => reopened.UnlockLayer(layer.Id, "not-the-password"));

        Assert.Equal(DecryptionException.DefaultMessage, error.Message);
    }

    [Fact]
    public void Locking_drops_the_key_and_the_cached_manifest()
    {
        var (workspace, layer, _) = Private();
        workspace.PrivateAccess(layer.Id).CreateNote(string.Empty, "Secret", "body");

        workspace.LockLayer(layer.Id);

        Assert.False(workspace.Encryption!.IsUnlocked(layer.Id));
        Assert.Throws<LayerLockedException>(() => workspace.PrivateAccess(layer.Id));
    }

    [Fact]
    public void Closing_the_workspace_locks_every_private_layer()
    {
        var (workspace, layer, _) = Private();

        workspace.Close();

        Assert.False(_fixture.Encryption.IsUnlocked(layer.Id));
    }

    [Fact]
    public void Lock_all_layers_counts_only_the_ones_it_locked()
    {
        var (workspace, first, _) = Private();
        var (second, _) = workspace.CreateLayer(
            "Second private", LayerVisibility.Private, password: WorkspaceFixture.Password);

        Assert.Equal(2, workspace.LockAllLayers());
        Assert.Equal(0, workspace.LockAllLayers());
        Assert.False(workspace.Encryption!.IsUnlocked(first.Id));
        Assert.False(workspace.Encryption!.IsUnlocked(second.Id));
    }

    [Fact]
    public void Locking_runs_teardown_hooks_even_when_one_throws()
    {
        var (workspace, layer, _) = Private();
        var cleared = new List<string>();

        _fixture.Encryption.OnLock(_ => throw new InvalidOperationException("bad hook"));
        _fixture.Encryption.OnLock(cleared.Add);

        workspace.LockLayer(layer.Id);

        Assert.Equal([layer.Id], cleared);
        Assert.True(_fixture.Logger.Logged("layer.teardown_hook_failed"));
    }

    [Fact]
    public void Private_operations_are_refused_on_a_public_layer()
    {
        var workspace = _fixture.Created();
        var layerId = _fixture.PublicLayerId;

        Assert.Throws<InvalidRequestException>(() => workspace.LockLayer(layerId));
        Assert.Throws<InvalidRequestException>(() => workspace.UnlockLayer(layerId, WorkspaceFixture.Password));
        Assert.Throws<InvalidRequestException>(() => workspace.RotateLayerKey(layerId, WorkspaceFixture.Password));
    }

    // -- remembered passwords ------------------------------------------------

    [Fact]
    public void A_remembered_password_unlocks_on_the_next_open()
    {
        var (workspace, layer, _) = Private();
        workspace.LockLayer(layer.Id);
        workspace.UnlockLayer(layer.Id, WorkspaceFixture.Password, remember: true);

        var reopened = new WorkspaceService(
            encryption: new EncryptionService(), layerPasswords: _fixture.Credentials);
        reopened.Open(_fixture.Root);

        Assert.Equal(LayerState.Unlocked, reopened.RequireLayer(layer.Id).State);
    }

    [Fact]
    public void Unlocking_without_remember_forgets_any_stored_password()
    {
        var (workspace, layer, _) = Private();
        workspace.UnlockLayer(layer.Id, WorkspaceFixture.Password, remember: true);
        Assert.True(_fixture.Credentials.Has(layer.Id));

        workspace.UnlockLayer(layer.Id, WorkspaceFixture.Password, remember: false);

        Assert.False(_fixture.Credentials.Has(layer.Id));
    }

    [Fact]
    public void A_stale_remembered_password_is_dropped_rather_than_retried_forever()
    {
        var (_, layer, _) = Private();
        _fixture.Credentials.Set(layer.Id, "no-longer-correct");

        var reopened = new WorkspaceService(
            encryption: new EncryptionService(),
            layerPasswords: _fixture.Credentials,
            logger: _fixture.Logger);
        reopened.Open(_fixture.Root);

        Assert.False(_fixture.Credentials.Has(layer.Id));
        Assert.Equal(LayerState.Locked, reopened.RequireLayer(layer.Id).State);
        Assert.True(_fixture.Logger.Logged("layer.remembered_password_rejected"));
    }

    [Fact]
    public void Changing_the_password_updates_a_remembered_one()
    {
        var (workspace, layer, _) = Private();
        workspace.UnlockLayer(layer.Id, WorkspaceFixture.Password, remember: true);

        workspace.ChangeLayerPassword(layer.Id, WorkspaceFixture.Password, "a-brand-new-password");

        Assert.Equal("a-brand-new-password", _fixture.Credentials.Get(layer.Id));

        var reopened = new WorkspaceService(encryption: new EncryptionService());
        reopened.Open(_fixture.Root);
        reopened.UnlockLayer(layer.Id, "a-brand-new-password");
        Assert.Equal(LayerState.Unlocked, reopened.RequireLayer(layer.Id).State);
    }

    [Fact]
    public void Forgetting_a_password_removes_it_from_the_keychain()
    {
        var (workspace, layer, _) = Private();
        workspace.UnlockLayer(layer.Id, WorkspaceFixture.Password, remember: true);

        workspace.ForgetLayerPassword(layer.Id);

        Assert.False(_fixture.Credentials.Has(layer.Id));
    }

    [Fact]
    public void Client_descriptors_carry_the_live_keychain_flag()
    {
        var (workspace, layer, _) = Private();
        workspace.UnlockLayer(layer.Id, WorkspaceFixture.Password, remember: true);

        Assert.True(workspace.LayerForClient(layer.Id).PasswordRemembered);
        Assert.False(workspace.LayerForClient(_fixture.PublicLayerId).PasswordRemembered);
    }

    // -- key management ------------------------------------------------------

    [Fact]
    public void A_reissued_recovery_key_replaces_the_old_one()
    {
        var (workspace, layer, oldRecovery) = Private();

        var newRecovery = workspace.ReissueRecoveryKey(layer.Id, WorkspaceFixture.Password);

        Assert.NotEqual(oldRecovery, newRecovery);

        var reopened = new WorkspaceService(encryption: new EncryptionService());
        reopened.Open(_fixture.Root);
        reopened.UnlockLayerWithRecoveryKey(layer.Id, newRecovery);
        Assert.Equal(LayerState.Unlocked, reopened.RequireLayer(layer.Id).State);
    }

    [Fact]
    public void Rotating_the_key_keeps_the_content_and_drops_the_recovery_key()
    {
        var (workspace, layer, recovery) = Private();
        var access = workspace.PrivateAccess(layer.Id);
        access.CreateNote(string.Empty, "Kept", "still here after rotation");

        var rewritten = workspace.RotateLayerKey(layer.Id, WorkspaceFixture.Password);

        Assert.True(rewritten >= 2); // the note plus the manifest
        var after = workspace.PrivateAccess(layer.Id);
        Assert.Equal("still here after rotation", after.ListNotes().Single().Content);

        // The old recovery key opened the *old* layer key: it must not survive.
        var reopened = new WorkspaceService(encryption: new EncryptionService());
        reopened.Open(_fixture.Root);
        Assert.Throws<DecryptionException>(() => reopened.UnlockLayerWithRecoveryKey(layer.Id, recovery!));
    }

    [Fact]
    public void The_password_still_opens_a_rotated_layer()
    {
        var (workspace, layer, _) = Private();
        workspace.PrivateAccess(layer.Id).CreateNote(string.Empty, "Note", "body");
        workspace.RotateLayerKey(layer.Id, WorkspaceFixture.Password);

        var reopened = new WorkspaceService(encryption: new EncryptionService());
        reopened.Open(_fixture.Root);
        reopened.UnlockLayer(layer.Id, WorkspaceFixture.Password);

        Assert.Equal("body", reopened.PrivateAccess(layer.Id).ListNotes().Single().Content);
    }

    [Fact]
    public void Rotation_bumps_the_key_generation()
    {
        var (workspace, layer, _) = Private();
        var before = LayerHeader.Load(workspace.LayerRoot(layer.Id)).KeyGeneration;

        workspace.RotateLayerKey(layer.Id, WorkspaceFixture.Password);

        Assert.Equal(before + 1, LayerHeader.Load(workspace.LayerRoot(layer.Id)).KeyGeneration);
    }

    // -- content through the access handle ------------------------------------

    [Fact]
    public void Notes_folders_and_attachments_round_trip_inside_a_private_layer()
    {
        var (workspace, layer, _) = Private();
        var access = workspace.PrivateAccess(layer.Id);

        access.CreateFolder(string.Empty, "Deals");
        var note = access.CreateNote("Deals", "Northwind", "Body with #deals",
            new JsonObject { ["status"] = "open" });
        var attachment = access.SaveAttachment("chart.png", [1, 2, 3]);

        Assert.Equal("Deals", Assert.Single(access.ListFolders()).Path);
        Assert.Equal("Northwind", Assert.Single(access.ListNotes()).Metadata.Title);
        Assert.Equal(["deals"], note.Metadata.Tags);
        Assert.StartsWith("strata-object://", attachment, StringComparison.Ordinal);
        Assert.Equal([1, 2, 3], access.ReadAttachment(attachment["strata-object://".Length..]));
    }

    [Fact]
    public void A_duplicate_title_in_the_same_folder_is_a_conflict()
    {
        var (workspace, layer, _) = Private();
        var access = workspace.PrivateAccess(layer.Id);
        access.CreateNote(string.Empty, "Same", "one");

        Assert.Throws<ConflictException>(() => access.CreateNote(string.Empty, "same", "two"));
        access.CreateNote("Elsewhere", "Same", "fine"); // a different folder is fine
    }

    [Fact]
    public void Renaming_keeps_the_object_id_so_links_survive()
    {
        var (workspace, layer, _) = Private();
        var access = workspace.PrivateAccess(layer.Id);
        var note = access.CreateNote(string.Empty, "Before", "body");

        var renamed = access.RenameNote(note.Metadata.Id, "After");

        Assert.Equal(note.Metadata.Id, renamed.Metadata.Id);
        Assert.Equal("After", renamed.Metadata.Title);
    }

    [Fact]
    public void Duplicating_picks_the_next_free_copy_name()
    {
        var (workspace, layer, _) = Private();
        var access = workspace.PrivateAccess(layer.Id);
        var note = access.CreateNote(string.Empty, "Doc", "body");

        Assert.Equal("Doc copy", access.DuplicateNote(note.Metadata.Id).Metadata.Title);
        Assert.Equal("Doc copy 2", access.DuplicateNote(note.Metadata.Id).Metadata.Title);
    }

    [Fact]
    public void Trashing_hides_a_note_without_decrypting_it_and_restoring_brings_it_back()
    {
        var (workspace, layer, _) = Private();
        var access = workspace.PrivateAccess(layer.Id);
        var note = access.CreateNote(string.Empty, "Doomed", "body");

        access.TrashNote(note.Metadata.Id);

        Assert.Empty(access.ListNotes());
        Assert.Single(access.ListTrash());
        Assert.Throws<NotFoundException>(() => access.GetNote(note.Metadata.Id));

        access.RestoreNote(note.Metadata.Id);
        Assert.Single(access.ListNotes());
    }

    [Fact]
    public void Emptying_the_trash_removes_the_ciphertext()
    {
        var (workspace, layer, _) = Private();
        var access = workspace.PrivateAccess(layer.Id);
        var note = access.CreateNote(string.Empty, "Doomed", "body");
        access.TrashNote(note.Metadata.Id);

        Assert.Equal(1, access.EmptyTrash());

        Assert.Empty(access.ListTrash());
        Assert.False(access.HasNote(note.Metadata.Id));
    }

    [Fact]
    public void Renaming_a_folder_moves_everything_beneath_it()
    {
        var (workspace, layer, _) = Private();
        var access = workspace.PrivateAccess(layer.Id);
        var folder = access.CreateFolder(string.Empty, "Old");
        access.CreateFolder("Old", "Inner");
        var note = access.CreateNote("Old/Inner", "Deep", "body");

        access.RenameFolder(folder.Id, "New");

        Assert.Equal("New/Inner", access.ListFolders().Single(f => f.Name == "Inner").Path);
        Assert.Equal("New/Inner", access.GetNote(note.Metadata.Id).Metadata.FolderPath);
    }

    [Fact]
    public void A_folder_cannot_be_moved_into_itself_or_a_descendant()
    {
        var (workspace, layer, _) = Private();
        var access = workspace.PrivateAccess(layer.Id);
        var folder = access.CreateFolder(string.Empty, "Outer");
        access.CreateFolder("Outer", "Inner");

        Assert.Throws<InvalidRequestException>(() => access.MoveFolder(folder.Id, "Outer"));
        Assert.Throws<InvalidRequestException>(() => access.MoveFolder(folder.Id, "Outer/Inner"));
    }

    [Fact]
    public void Deleting_a_folder_trashes_the_notes_under_it()
    {
        var (workspace, layer, _) = Private();
        var access = workspace.PrivateAccess(layer.Id);
        var folder = access.CreateFolder(string.Empty, "Doomed");
        access.CreateNote("Doomed", "One", "a");
        access.CreateNote("Doomed", "Two", "b");
        access.CreateNote(string.Empty, "Kept", "c");

        Assert.Equal(2, access.DeleteFolder(folder.Id));

        Assert.Equal(["Kept"], access.ListNotes().Select(note => note.Metadata.Title));
        Assert.Empty(access.ListFolders());
    }

    [Fact]
    public void A_title_that_would_escape_is_sanitised_before_it_reaches_the_manifest()
    {
        var (workspace, layer, _) = Private();

        var note = workspace.PrivateAccess(layer.Id).CreateNote(string.Empty, "../../evil", "body");

        Assert.Equal("evil", note.Metadata.Title);
    }
}
