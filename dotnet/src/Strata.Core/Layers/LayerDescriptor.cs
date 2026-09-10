using System.Text.Json.Serialization;

namespace Strata.Core.Layers;

/// <summary>
/// Layer domain model — port of <c>app/domain/layer.py</c>.
/// </summary>
/// <remarks>
/// A <em>layer</em> is an independent content, permission, encryption,
/// synchronisation and AI-access boundary. Every knowledge object belongs to
/// exactly one layer.
/// </remarks>
[JsonConverter(typeof(JsonStringEnumConverter<LayerVisibility>))]
public enum LayerVisibility
{
    [JsonStringEnumMemberName("public")] Public,
    [JsonStringEnumMemberName("private")] Private,
}

[JsonConverter(typeof(JsonStringEnumConverter<LayerState>))]
public enum LayerState
{
    [JsonStringEnumMemberName("mounted")] Mounted,
    [JsonStringEnumMemberName("unmounted")] Unmounted,
    [JsonStringEnumMemberName("locked")] Locked,
    [JsonStringEnumMemberName("unlocked")] Unlocked,
}

[JsonConverter(typeof(JsonStringEnumConverter<LayerSharingMode>))]
public enum LayerSharingMode
{
    [JsonStringEnumMemberName("personal")] Personal,
    [JsonStringEnumMemberName("shared-password")] SharedPassword,
    [JsonStringEnumMemberName("identity-managed")] IdentityManaged,
}

/// <summary>
/// How a layer's bytes are kept on disk.
/// </summary>
/// <remarks>
/// Deliberately <em>not</em> the same axis as visibility: "private" is a policy
/// (who may read it, what the AI may do with it, whether an export needs a
/// confirmation), while storage is a mechanism. Keeping them separate means the
/// privacy rules are enforced against the descriptor, and adding a storage
/// backend does not re-thread every privacy check.
/// </remarks>
[JsonConverter(typeof(JsonStringEnumConverter<LayerStorage>))]
public enum LayerStorage
{
    [JsonStringEnumMemberName("markdown")] Markdown,
    [JsonStringEnumMemberName("encrypted-objects")] EncryptedObjects,
}

[JsonConverter(typeof(JsonStringEnumConverter<AIAccess>))]
public enum AIAccess
{
    [JsonStringEnumMemberName("disabled")] Disabled,
    [JsonStringEnumMemberName("local-only")] LocalOnly,
    [JsonStringEnumMemberName("remote-with-confirmation")] RemoteWithConfirmation,
    [JsonStringEnumMemberName("remote-always")] RemoteAlways,
}

[JsonConverter(typeof(JsonStringEnumConverter<EmbeddingAccess>))]
public enum EmbeddingAccess
{
    [JsonStringEnumMemberName("disabled")] Disabled,
    [JsonStringEnumMemberName("local-only")] LocalOnly,
    [JsonStringEnumMemberName("remote-allowed")] RemoteAllowed,
}

/// <summary>
/// Per-layer AI permissions.
/// </summary>
/// <remarks>
/// Enforced in the service layer, never in the UI: the UI only mirrors it.
/// A locked layer is never available to AI regardless of this policy.
/// </remarks>
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record LayerAIPolicy
{
    public AIAccess Access { get; init; } = AIAccess.LocalOnly;

    public EmbeddingAccess Embeddings { get; init; } = EmbeddingAccess.LocalOnly;

    public bool MayRead { get; init; } = true;

    public bool MaySummarize { get; init; } = true;

    public bool MayProposeEdits { get; init; } = true;

    public bool MayApplyApprovedEdits { get; init; }

    public bool MayCreateLinks { get; init; }

    public bool MayReorganizeStructure { get; init; }

    public bool MayProcessAttachments { get; init; }

    [JsonIgnore]
    public bool AllowsRemote =>
        Access is AIAccess.RemoteWithConfirmation or AIAccess.RemoteAlways;

    [JsonIgnore]
    public bool RequiresConfirmationForRemote => Access is AIAccess.RemoteWithConfirmation;
}

/// <summary>
/// The public description of a layer.
/// </summary>
/// <remarks>
/// Safe to send to the frontend in any lock state: for a private layer the
/// <see cref="DisplayName"/> is user-chosen metadata stored in the <em>workspace</em>
/// file, not inside the encrypted layer, so it is available while locked. Nothing
/// else about a locked layer's contents is exposed.
/// </remarks>
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record LayerDescriptor
{
    public const int LayerStorageVersion = 1;

    public required string Id { get; init; }

    public required string DisplayName { get; init; }

    public LayerVisibility Visibility { get; init; } = LayerVisibility.Public;

    public LayerState State { get; init; } = LayerState.Mounted;

    public LayerSharingMode SharingMode { get; init; } = LayerSharingMode.Personal;

    public LayerStorage Storage { get; init; } = LayerStorage.Markdown;

    public int StorageVersion { get; init; } = LayerStorageVersion;

    public required string CreatedAt { get; init; }

    public required string UpdatedAt { get; init; }

    public string Color { get; init; } = "layer-public";

    public LayerAIPolicy AiPolicy { get; init; } = new();

    public bool PasswordRemembered { get; init; }

    /// <summary>True when the app currently holds what it needs to read the layer.</summary>
    [JsonIgnore]
    public bool IsReadable => Visibility == LayerVisibility.Public
        ? State is LayerState.Mounted or LayerState.Unlocked
        : State is LayerState.Unlocked;

    [JsonIgnore]
    public bool IsLocked => Visibility == LayerVisibility.Private && State != LayerState.Unlocked;
}

/// <summary>A cross-layer reference. Always opaque: never a path, never a title.</summary>
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record KnowledgeObjectRef(string LayerId, string ObjectId)
{
    [JsonIgnore]
    public string Key => $"{LayerId}:{ObjectId}";
}
