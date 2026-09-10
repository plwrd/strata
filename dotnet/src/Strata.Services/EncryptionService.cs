using Strata.Core.Errors;
using Strata.Core.Logging;
using Strata.Infrastructure.Encryption;
using Strata.Infrastructure.Storage;

namespace Strata.Services;

/// <summary>
/// Private-layer lifecycle: create, unlock, lock, change password, rotate — port of
/// <c>app/services/encryption_service.py</c>.
/// </summary>
/// <remarks>
/// <para>
/// This service owns the only path from a password to a key. Everything else asks
/// the <see cref="KeyHolder"/>, and the KeyHolder only has a key if this service
/// put one there.
/// </para>
/// <para>
/// The teardown on lock is the part that is easy to get wrong. Removing the key
/// stops <em>future</em> reads, but the decrypted content already handed to the
/// search index, the graph labels, the editor buffer and the AI context does not
/// vanish on its own. So <see cref="Lock"/> runs a registered set of teardown
/// hooks, and anything that caches decrypted private content is required to
/// register one — see <see cref="OnLock"/>.
/// </para>
/// </remarks>
public sealed class EncryptionService(KeyHolder? keys = null, IStrataLogger? logger = null)
{
    public const int MinPasswordLength = 8;

    private readonly IStrataLogger _logger = logger ?? NullLogger.Instance;
    private readonly List<Action<string>> _teardown = [];

    public KeyHolder Keys { get; } = keys ?? new KeyHolder(logger);

    private static string Now() => MarkdownLayerStore.NowIso();

    /// <summary>
    /// Register a teardown hook, called with the layer id when it locks.
    /// </summary>
    /// <remarks>
    /// Anything that holds decrypted private content must register one: search
    /// indexes, embedding matrices, graph labels, editor buffers, previews, AI
    /// context. A component that caches and does not register is a leak that
    /// survives the lock.
    /// </remarks>
    public void OnLock(Action<string> hook) => _teardown.Add(hook);

    // -- creation ------------------------------------------------------------

    /// <summary>Create a private layer. Returns its header and the recovery key (once).</summary>
    public (LayerHeader Header, string? RecoveryKey) CreateLayer(
        string layerId,
        string root,
        string password,
        bool withRecoveryKey = true,
        bool padding = true)
    {
        RequirePasswordLength(password);

        if (File.Exists(Path.Combine(root, LayerHeader.HeaderFilename)))
        {
            throw new ConflictException("This layer already exists.");
        }

        var recoveryKey = withRecoveryKey ? LayerHeader.GenerateRecoveryKey() : null;
        var (header, layerKey) = LayerHeader.Create(layerId, password, Now(), recoveryKey);
        header.PaddingEnabled = padding;

        var store = new EncryptedLayerStore(layerId, root, padding);
        store.Ensure();
        header.ManifestObjectId = store.CreateManifest(layerKey);
        header.Save(root);

        Keys.Unlock(layerId, layerKey);
        AeadPrimitives.Zeroize(layerKey);
        _logger.Info("layer.created_private", ("layer_id", layerId), ("recovery", recoveryKey is not null));

        // Shown once, never stored. If the user loses it along with the password,
        // the layer is gone — and we say so rather than keeping a copy "to help".
        return (header, recoveryKey);
    }

    // -- unlock / lock -------------------------------------------------------

    public LayerHeader Unlock(string layerId, string root, string password)
    {
        var header = LoadHeader(root, layerId);

        byte[] layerKey;
        try
        {
            layerKey = header.UnlockWithPassword(password);
        }
        catch (DecryptionException)
        {
            // One generic failure. It does not say whether the password was wrong,
            // whether the layer is corrupt, or whether anything is inside.
            _logger.Warning("layer.unlock_failed", ("layer_id", layerId));
            throw;
        }

        Keys.Unlock(layerId, layerKey);
        ResumeRotation(layerId, root, layerKey, password);
        return header;
    }

    public LayerHeader UnlockWithRecoveryKey(string layerId, string root, string recoveryKey)
    {
        var header = LoadHeader(root, layerId);
        var layerKey = header.UnlockWithRecoveryKey(recoveryKey);

        Keys.Unlock(layerId, layerKey);
        _logger.Info("layer.unlocked_via_recovery", ("layer_id", layerId));
        ResumeRotation(layerId, root, layerKey, password: null);
        return header;
    }

    /// <summary>Drop the key and tear down everything that cached decrypted content.</summary>
    public bool Lock(string layerId)
    {
        var locked = Keys.Lock(layerId);
        foreach (var hook in _teardown)
        {
            try
            {
                hook(layerId);
            }
            catch (Exception exc)
            {
                // A hook that throws must not strand the other hooks: the key is
                // already gone, and the remaining caches still have to be cleared.
                _logger.Exception(exc, "layer.teardown_hook_failed", ("layer_id", layerId));
            }
        }

        return locked;
    }

    public int LockAll() => Keys.UnlockedLayers().Count(Lock);

    public bool IsUnlocked(string layerId) => Keys.IsUnlocked(layerId);

    // -- key management ------------------------------------------------------

    /// <summary>Rewrap the layer key. Cheap, and does <em>not</em> revoke anyone.</summary>
    public LayerHeader ChangePassword(string layerId, string root, string oldPassword, string newPassword)
    {
        RequirePasswordLength(newPassword);

        var header = LoadHeader(root, layerId);
        header.ChangePassword(oldPassword, newPassword);
        header.UpdatedAt = Now();
        header.Save(root);

        _logger.Info("layer.password_changed", ("layer_id", layerId));
        return header;
    }

    public string ReissueRecoveryKey(string layerId, string root, string password)
    {
        var header = LoadHeader(root, layerId);
        var recoveryKey = LayerHeader.GenerateRecoveryKey();
        header.SetRecoveryKey(password, recoveryKey);
        header.UpdatedAt = Now();
        header.Save(root);
        return recoveryKey;
    }

    /// <summary>
    /// Generate a new layer key and re-encrypt every object under it.
    /// </summary>
    /// <remarks>
    /// This is the operation that actually revokes a former collaborator — a
    /// password change does not, because they may already hold the layer key.
    /// Progress is journalled: the new key is stored wrapped with the old key, and
    /// each rewritten object id is recorded before the header is committed. A crash
    /// mid-rotation is resumed on the next unlock or rotate.
    /// </remarks>
    public int RotateKey(string layerId, string root, string password)
    {
        var header = LoadHeader(root, layerId);
        var oldKey = header.UnlockWithPassword(password);
        return FinishRotation(layerId, root, header, oldKey, password);
    }

    private void ResumeRotation(string layerId, string root, byte[] oldKey, string? password)
    {
        if (!new RotationJournal(root).Exists())
        {
            return;
        }

        try
        {
            var header = LoadHeader(root, layerId);
            FinishRotation(layerId, root, header, oldKey, password);
        }
        catch (Exception exc)
        {
            // A resume that fails must not block the unlock that triggered it: the
            // layer is still readable on the key we just recovered.
            _logger.Exception(exc, "layer.rotation_resume_failed", ("layer_id", layerId));
        }
    }

    private int FinishRotation(string layerId, string root, LayerHeader header, byte[] oldKey, string? password)
    {
        var journal = new RotationJournal(root);

        byte[] newKey;
        HashSet<string> done;
        if (journal.Exists())
        {
            var state = journal.Load(oldKey);
            newKey = state.NewKey;
            done = [.. state.DoneObjectIds];
        }
        else
        {
            newKey = AeadPrimitives.RandomKey();
            done = [];
            journal.Save(layerId, header.ManifestObjectId, header.PaddingEnabled, [], oldKey, newKey);
        }

        var store = new EncryptedLayerStore(layerId, root, header.PaddingEnabled);

        void Persist(string objectId)
        {
            done.Add(objectId);
            journal.Save(
                layerId,
                header.ManifestObjectId,
                header.PaddingEnabled,
                done.Order(StringComparer.Ordinal).ToList(),
                oldKey,
                newKey);
        }

        var rewritten = store.Rotate(
            oldKey, newKey, header.ManifestObjectId, done: new HashSet<string>(done), onProgress: Persist);

        if (password is null)
        {
            // Unlock-time resume: objects are on the new key, but committing the
            // header requires the password (to rewrap). Keep the journal and hold
            // the new key in memory so the layer is usable until rotate finishes.
            Keys.Unlock(layerId, newKey);
            _logger.Warning("layer.rotation_incomplete_header", ("layer_id", layerId));
            return rewritten;
        }

        header.RewrapForRotation(password, newKey);
        header.UpdatedAt = Now();
        header.Save(root);
        journal.Clear();

        Keys.Unlock(layerId, newKey);
        _logger.Info(
            "layer.key_rotated",
            ("layer_id", layerId),
            ("objects", rewritten),
            ("generation", header.KeyGeneration));
        return rewritten;
    }

    // -- helpers -------------------------------------------------------------

    private static void RequirePasswordLength(string password)
    {
        if (password.Length < MinPasswordLength)
        {
            throw new InvalidRequestException(
                $"A layer password must be at least {MinPasswordLength} characters.",
                new Dictionary<string, object?> { ["minimum"] = MinPasswordLength });
        }
    }

    private static LayerHeader LoadHeader(string root, string layerId)
    {
        var header = LayerHeader.Load(root);
        if (header.LayerId.Length > 0 && header.LayerId != layerId)
        {
            throw new NotFoundException("Layer not found.");
        }

        return header;
    }

    public static EncryptedLayerStore StoreFor(string layerId, string root, LayerHeader header)
        => new(layerId, root, header.PaddingEnabled);
}
