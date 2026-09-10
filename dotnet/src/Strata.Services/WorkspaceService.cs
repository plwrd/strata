using Strata.Core;
using Strata.Core.Errors;
using Strata.Core.Layers;
using Strata.Core.Logging;
using Strata.Core.Schema;
using Strata.Core.Views;
using Strata.Core.Workspaces;
using Strata.Infrastructure.Encryption;
using Strata.Infrastructure.Keychain;
using Strata.Infrastructure.Storage;

namespace Strata.Services;

/// <summary>
/// Workspace and layer lifecycle — port of
/// <c>app/services/workspace_service.py</c>.
/// </summary>
/// <remarks>
/// Holds the <em>one</em> open workspace and the per-layer stores. Every other
/// service asks this one for a readable layer, which is where the lock check lives
/// — so a lock check cannot be forgotten by a caller.
/// </remarks>
public sealed class WorkspaceService(
    Action<string>? onOpen = null,
    Action? onClose = null,
    EncryptionService? encryption = null,
    ICredentialStore? layerPasswords = null,
    IStrataLogger? logger = null)
{
    private readonly IStrataLogger _logger = logger ?? NullLogger.Instance;
    private readonly Dictionary<string, MarkdownLayerStore> _layerStores = new(StringComparer.Ordinal);
    private readonly Dictionary<string, PrivateLayerAccess> _privateAccess = new(StringComparer.Ordinal);

    private WorkspaceStore? _store;
    private WorkspaceDescriptor? _descriptor;

    /// <summary>
    /// Seeds a freshly created workspace with sample content. Injected rather than
    /// imported: <c>demo_content.py</c> is not ported, and the lifecycle does not
    /// need to know what demo content is.
    /// </summary>
    public Action<MarkdownLayerStore>? SeedDemoContent { get; set; }

    // -- state ---------------------------------------------------------------

    public bool IsOpen => _descriptor is not null;

    /// <summary>
    /// The encryption service this workspace was built with, or null in a build
    /// without encryption. Exposed because the service container hands the same
    /// instance to everything that registers a lock teardown hook.
    /// </summary>
    public EncryptionService? Encryption => encryption;

    public WorkspaceDescriptor Descriptor =>
        _descriptor ?? throw new NotFoundException("No workspace is open.");

    public string Root => RequireStore().Root;

    /// <summary>The on-disk root for a layer's objects.</summary>
    public string LayerRoot(string layerId) => RequireStore().LayerRoot(layerId);

    private WorkspaceStore RequireStore()
        => _store ?? throw new NotFoundException("No workspace is open.");

    private void SetDescriptor(WorkspaceDescriptor descriptor) => _descriptor = descriptor;

    // -- lifecycle -----------------------------------------------------------

    public WorkspaceDescriptor Create(string root, string name, bool seedDemo = false)
    {
        var store = new WorkspaceStore(root);
        if (store.Exists())
        {
            throw new InvalidRequestException("A workspace already exists at this location.");
        }

        var timestamp = MarkdownLayerStore.NowIso();
        var descriptor = new WorkspaceDescriptor
        {
            Id = Ids.NewWorkspaceId(),
            Name = name,
            CreatedAt = timestamp,
            UpdatedAt = timestamp,
        };

        store.Initialise(descriptor);
        _store = store;
        SetDescriptor(descriptor);
        _layerStores.Clear();

        var (layer, _) = CreateLayer("Knowledge", LayerVisibility.Public);

        // The knowledge loop's default areas. Folders, not walls — an existing
        // workspace is never forced into them.
        var markdownStore = LayerStore(layer.Id);
        foreach (var area in KnowledgeAreas.All)
        {
            Directory.CreateDirectory(Path.Combine(markdownStore.Root, area));
        }

        if (seedDemo)
        {
            SeedDemoContent?.Invoke(markdownStore);
        }

        InstallDefaultLenses();
        _logger.Info("workspace.created", ("workspace_id", descriptor.Id));
        onOpen?.Invoke(root);
        return Descriptor;
    }

    public WorkspaceDescriptor Open(string root)
    {
        var store = new WorkspaceStore(root);
        var descriptor = store.Load();

        // A freshly-opened workspace holds no keys, so every private layer is
        // locked — regardless of what the file says. `state` is JSON on disk, and
        // trusting it would let anyone who edits workspace.json flip a layer to
        // "unlocked" and have the UI believe it.
        descriptor = descriptor with
        {
            Layers = descriptor.Layers
                .Select(layer => layer.Visibility == LayerVisibility.Private
                    ? layer with { State = LayerState.Locked }
                    : layer)
                .ToList(),
        };

        _store = store;
        SetDescriptor(descriptor);
        _layerStores.Clear();
        _privateAccess.Clear();

        _logger.Info("workspace.opened",
            ("workspace_id", descriptor.Id), ("layers", descriptor.Layers.Count));
        onOpen?.Invoke(root);
        UnlockRememberedLayers();
        return Descriptor;
    }

    /// <summary>Open the workspace at <paramref name="root"/>, creating a seeded one if absent.</summary>
    public WorkspaceDescriptor OpenOrCreate(string root, string name)
        => new WorkspaceStore(root).Exists() ? Open(root) : Create(root, name, seedDemo: true);

    public void Close()
    {
        // Closing the workspace locks every private layer. Leaving a key in memory
        // after the user closed the thing it belongs to would be indefensible.
        encryption?.LockAll();
        onClose?.Invoke();

        _store = null;
        _descriptor = null;
        _layerStores.Clear();
        _privateAccess.Clear();
    }

    private void Save()
    {
        SetDescriptor(Descriptor with { UpdatedAt = MarkdownLayerStore.NowIso() });
        RequireStore().Save(Descriptor);
    }

    /// <summary>Replace one layer in the descriptor. Records are immutable, so this rebuilds the list.</summary>
    private LayerDescriptor Replace(LayerDescriptor layer)
    {
        SetDescriptor(Descriptor with
        {
            Layers = Descriptor.Layers.Select(existing => existing.Id == layer.Id ? layer : existing).ToList(),
        });
        return layer;
    }

    // -- layers --------------------------------------------------------------

    /// <summary>
    /// Create a layer. Returns the descriptor and, for a private layer, the recovery
    /// key — which is shown once and never stored.
    /// </summary>
    public (LayerDescriptor Layer, string? RecoveryKey) CreateLayer(
        string displayName,
        LayerVisibility visibility = LayerVisibility.Public,
        LayerAIPolicy? aiPolicy = null,
        string? password = null,
        bool withRecoveryKey = true,
        bool padding = true)
    {
        var timestamp = MarkdownLayerStore.NowIso();
        var store = RequireStore();
        var layerId = Ids.NewLayerId();
        string? recoveryKey = null;

        LayerStorage storage;
        LayerState state;

        if (visibility == LayerVisibility.Private)
        {
            if (encryption is null)
            {
                throw new UnsupportedException("Encryption is not available in this build.");
            }

            if (string.IsNullOrEmpty(password))
            {
                throw new InvalidRequestException("A private layer needs a password.");
            }

            (_, recoveryKey) = encryption.CreateLayer(
                layerId, store.LayerRoot(layerId), password, withRecoveryKey, padding);

            storage = LayerStorage.EncryptedObjects;

            // A private layer is created *unlocked*: the user just proved they hold
            // the password by choosing it.
            state = LayerState.Unlocked;
        }
        else
        {
            new MarkdownLayerStore(layerId, store.LayerRoot(layerId)).Ensure();
            storage = LayerStorage.Markdown;
            state = LayerState.Mounted;
        }

        var layer = new LayerDescriptor
        {
            Id = layerId,
            DisplayName = displayName,
            Visibility = visibility,
            State = state,
            Storage = storage,
            CreatedAt = timestamp,
            UpdatedAt = timestamp,
            Color = visibility == LayerVisibility.Private ? "layer-private" : "layer-public",

            // A private layer defaults to local-only AI. Opting in to a remote model
            // with private content has to be a decision, not a default.
            AiPolicy = aiPolicy ?? new LayerAIPolicy(),
        };

        SetDescriptor(Descriptor with
        {
            Layers = [.. Descriptor.Layers, layer],
            LayerOrder = [.. Descriptor.LayerOrder, layer.Id],
        });
        Save();

        _logger.Info("layer.created", ("layer_id", layer.Id), ("visibility", visibility.ToString()));
        return (layer, recoveryKey);
    }

    /// <summary>
    /// Change a layer's AI policy — and persist it. A policy that silently reverted
    /// on restart would be a lie the settings screen told.
    /// </summary>
    public LayerDescriptor SetLayerAiPolicy(string layerId, LayerAIPolicy policy)
    {
        var layer = RequireLayer(layerId) with { AiPolicy = policy, UpdatedAt = MarkdownLayerStore.NowIso() };
        Replace(layer);
        Save();

        _logger.Info("layer.ai_policy_changed", ("layer_id", layerId), ("access", policy.Access.ToString()));
        return layer;
    }

    public LayerDescriptor RenameLayer(string layerId, string displayName)
    {
        var layer = RequireLayer(layerId) with
        {
            DisplayName = displayName,
            UpdatedAt = MarkdownLayerStore.NowIso(),
        };
        Replace(layer);
        Save();
        return layer;
    }

    public WorkspaceDescriptor ReorderLayers(IReadOnlyList<string> layerOrder)
    {
        var known = Descriptor.Layers.Select(layer => layer.Id).ToHashSet(StringComparer.Ordinal);
        if (!known.SetEquals(layerOrder) || layerOrder.Count != known.Count)
        {
            throw new InvalidRequestException("The layer order must list every layer exactly once.");
        }

        SetDescriptor(Descriptor with { LayerOrder = [.. layerOrder] });
        Save();
        return Descriptor;
    }

    public LayerDescriptor RequireLayer(string layerId)
        => Descriptor.Layer(layerId) ?? throw new NotFoundException("Layer not found.");

    private bool HoldsKey(string layerId) => encryption is not null && encryption.IsUnlocked(layerId);

    /// <summary>
    /// The single place a lock check happens. Callers cannot skip it.
    /// </summary>
    /// <remarks>
    /// For a private layer the truth is the <em>key holder</em>, not the
    /// descriptor's <c>state</c> field — that is JSON on disk, and a bug (or someone
    /// with a text editor) could set it to "unlocked". Without the key nothing
    /// decrypts anyway; checking here just makes the failure early and legible.
    /// </remarks>
    public LayerDescriptor RequireReadableLayer(string layerId)
    {
        var layer = RequireLayer(layerId);
        if (layer.Visibility == LayerVisibility.Private)
        {
            if (!HoldsKey(layerId))
            {
                // Deliberately generic: does not confirm whether anything inside the
                // layer matches the request.
                throw new LayerLockedException(
                    "This layer is locked.",
                    new Dictionary<string, object?> { ["layerId"] = layerId });
            }

            return layer;
        }

        if (layer.IsLocked)
        {
            throw new LayerLockedException(
                "This layer is locked.",
                new Dictionary<string, object?> { ["layerId"] = layerId });
        }

        return layer;
    }

    private bool IsReadable(LayerDescriptor layer)
        => layer.Visibility == LayerVisibility.Private ? HoldsKey(layer.Id) : layer.IsReadable;

    public List<LayerDescriptor> ReadableLayers()
        => Descriptor.OrderedLayers().Where(IsReadable).ToList();

    public List<LayerDescriptor> LockedLayers()
        => Descriptor.OrderedLayers().Where(layer => !IsReadable(layer)).ToList();

    /// <summary>The Markdown store for a readable layer with Markdown storage.</summary>
    public MarkdownLayerStore LayerStore(string layerId)
    {
        var layer = RequireReadableLayer(layerId);
        if (layer.Storage != LayerStorage.Markdown)
        {
            throw new InvalidRequestException("This layer does not use Markdown storage.");
        }

        if (!_layerStores.TryGetValue(layerId, out var store))
        {
            store = new MarkdownLayerStore(layerId, RequireStore().LayerRoot(layerId));
            store.Ensure();
            _layerStores[layerId] = store;
        }

        return store;
    }

    // -- private layers ------------------------------------------------------

    private EncryptionService RequireEncryption()
        => encryption ?? throw new UnsupportedException("Encryption is not available in this build.");

    public LayerDescriptor UnlockLayer(string layerId, string password, bool remember = false)
    {
        var layer = RequirePrivate(layerId);
        RequireEncryption().Unlock(layerId, RequireStore().LayerRoot(layerId), password);
        RememberPassword(layerId, password, remember);
        return MarkUnlocked(layer);
    }

    public LayerDescriptor UnlockLayerWithRecoveryKey(string layerId, string recoveryKey)
    {
        var layer = RequirePrivate(layerId);
        RequireEncryption().UnlockWithRecoveryKey(layerId, RequireStore().LayerRoot(layerId), recoveryKey);
        return MarkUnlocked(layer);
    }

    private LayerDescriptor MarkUnlocked(LayerDescriptor layer)
    {
        var unlocked = Replace(layer with { State = LayerState.Unlocked });
        _privateAccess.Remove(layer.Id);
        Save();
        return unlocked;
    }

    public LayerDescriptor LockLayer(string layerId)
    {
        var layer = RequirePrivate(layerId);
        RequireEncryption().Lock(layerId);

        // Drop the cached handle: it holds a decrypted manifest, which is every
        // title, tag and folder name in the layer.
        _privateAccess.Remove(layerId);

        var locked = Replace(layer with { State = LayerState.Locked });
        Save();
        return locked;
    }

    public int LockAllLayers()
    {
        var count = 0;
        foreach (var layer in Descriptor.Layers.ToList())
        {
            if (layer.Visibility == LayerVisibility.Private && HoldsKey(layer.Id))
            {
                LockLayer(layer.Id);
                count++;
            }
        }

        return count;
    }

    public void ChangeLayerPassword(string layerId, string oldPassword, string newPassword)
    {
        RequirePrivate(layerId);
        RequireEncryption().ChangePassword(
            layerId, RequireStore().LayerRoot(layerId), oldPassword, newPassword);

        if (layerPasswords is not null && layerPasswords.Has(layerId))
        {
            layerPasswords.Set(layerId, newPassword);
        }
    }

    public void ForgetLayerPassword(string layerId)
    {
        RequirePrivate(layerId);
        layerPasswords?.Delete(layerId);
    }

    private void RememberPassword(string layerId, string password, bool remember)
    {
        if (layerPasswords is null)
        {
            return;
        }

        if (remember)
        {
            if (!layerPasswords.Set(layerId, password))
            {
                _logger.Warning("layer.password_remember_failed", ("layer_id", layerId));
            }

            return;
        }

        layerPasswords.Delete(layerId);
    }

    private void UnlockRememberedLayers()
    {
        if (layerPasswords is null || encryption is null)
        {
            return;
        }

        foreach (var layer in Descriptor.Layers.ToList())
        {
            if (layer.Visibility != LayerVisibility.Private || layer.State == LayerState.Unlocked)
            {
                continue;
            }

            var password = layerPasswords.Get(layer.Id);
            if (string.IsNullOrEmpty(password))
            {
                continue;
            }

            try
            {
                UnlockLayer(layer.Id, password, remember: true);
            }
            catch (Exception exc) when (exc is DecryptionException or InvalidRequestException or NotFoundException)
            {
                layerPasswords.Delete(layer.Id);
                _logger.Warning("layer.remembered_password_rejected", ("layer_id", layer.Id));
            }
        }
    }

    /// <summary>Layer descriptors with live keychain flags, safe to send to the UI.</summary>
    public List<LayerDescriptor> LayersForClient()
        => Descriptor.OrderedLayers()
            .Select(layer => layer with
            {
                PasswordRemembered = layer.Visibility == LayerVisibility.Private
                    && layerPasswords is not null
                    && layerPasswords.Has(layer.Id),
            })
            .ToList();

    public LayerDescriptor LayerForClient(string layerId)
        => LayersForClient().FirstOrDefault(layer => layer.Id == layerId) ?? RequireLayer(layerId);

    public string ReissueRecoveryKey(string layerId, string password)
    {
        RequirePrivate(layerId);
        return RequireEncryption().ReissueRecoveryKey(layerId, RequireStore().LayerRoot(layerId), password);
    }

    public int RotateLayerKey(string layerId, string password)
    {
        RequirePrivate(layerId);
        var rewritten = RequireEncryption().RotateKey(layerId, RequireStore().LayerRoot(layerId), password);
        _privateAccess.Remove(layerId);
        return rewritten;
    }

    private LayerDescriptor RequirePrivate(string layerId)
    {
        var layer = RequireLayer(layerId);
        if (layer.Visibility != LayerVisibility.Private)
        {
            throw new InvalidRequestException("This operation applies only to private layers.");
        }

        return layer;
    }

    /// <summary>
    /// A working handle on an unlocked private layer.
    /// </summary>
    /// <remarks>
    /// Obtaining one <em>is</em> the lock check: <see cref="RequireReadableLayer"/>
    /// runs first, and the key comes from the key holder, which is empty while
    /// locked.
    /// </remarks>
    public PrivateLayerAccess PrivateAccess(string layerId)
    {
        var layer = RequireReadableLayer(layerId);
        if (layer.Storage != LayerStorage.EncryptedObjects)
        {
            throw new InvalidRequestException("This layer is not encrypted.");
        }

        if (_privateAccess.TryGetValue(layerId, out var cached))
        {
            return cached;
        }

        var service = RequireEncryption();
        var root = RequireStore().LayerRoot(layerId);
        var access = new PrivateLayerAccess(layerId, root, service.Keys.KeyFor(layerId), LayerHeader.Load(root));
        _privateAccess[layerId] = access;
        return access;
    }

    // -- lenses --------------------------------------------------------------

    private void InstallDefaultLenses()
    {
        var allLayers = Descriptor.Layers.Select(layer => layer.Id).ToList();
        var publicLayers = Descriptor.Layers
            .Where(layer => layer.Visibility == LayerVisibility.Public)
            .Select(layer => layer.Id)
            .ToList();

        SetDescriptor(Descriptor with
        {
            Lenses =
            [
                new KnowledgeLens
                {
                    Id = "lens_all",
                    Name = "All Knowledge",
                    VisibleLayerIds = allLayers,
                    LayerOrder = [.. allLayers],
                    AiReadableLayerIds = allLayers,
                    IsDefault = true,
                },
                new KnowledgeLens
                {
                    Id = "lens_recent",
                    Name = "Recently Modified",
                    VisibleLayerIds = allLayers,
                    LayerOrder = [.. allLayers],
                    AiReadableLayerIds = allLayers,
                    TimeRangeDays = 14,
                },
                new KnowledgeLens
                {
                    Id = "lens_public",
                    Name = "Public Documentation",
                    VisibleLayerIds = publicLayers,
                    LayerOrder = [.. allLayers],
                    AiReadableLayerIds = publicLayers,
                },
            ],
        });
        Save();
    }

    public List<KnowledgeLens> Lenses() => [.. Descriptor.Lenses];

    public KnowledgeLens SaveLens(KnowledgeLens lens)
    {
        SetDescriptor(Descriptor with { Lenses = Upsert(Descriptor.Lenses, lens, saved => saved.Id == lens.Id) });
        Save();
        return lens;
    }

    // -- saved views ---------------------------------------------------------

    public List<ViewConfig> SavedViews() => [.. Descriptor.SavedViews];

    public ViewConfig SaveView(ViewConfig view)
    {
        SetDescriptor(Descriptor with
        {
            SavedViews = Upsert(Descriptor.SavedViews, view, saved => saved.Id == view.Id),
        });
        Save();
        return view;
    }

    public void DeleteView(string viewId)
    {
        SetDescriptor(Descriptor with
        {
            SavedViews = Descriptor.SavedViews.Where(view => view.Id != viewId).ToList(),
        });
        Save();
    }

    /// <summary>Replace the matching item in place, or append it. Order is stable either way.</summary>
    private static List<T> Upsert<T>(IReadOnlyList<T> items, T item, Func<T, bool> matches)
    {
        var updated = items.ToList();
        var index = updated.FindIndex(existing => matches(existing));
        if (index < 0)
        {
            updated.Add(item);
        }
        else
        {
            updated[index] = item;
        }

        return updated;
    }
}
