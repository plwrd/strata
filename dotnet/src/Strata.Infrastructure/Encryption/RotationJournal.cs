using System.Text.Json;
using System.Text.Json.Nodes;
using Strata.Core.Errors;
using Strata.Infrastructure.Storage;

namespace Strata.Infrastructure.Encryption;

/// <summary>The state a half-finished rotation left behind.</summary>
public sealed record RotationJournalState(
    string LayerId,
    string ManifestObjectId,
    bool PaddingEnabled,
    IReadOnlyList<string> DoneObjectIds,
    byte[] NewKey);

/// <summary>
/// Crash-safe key-rotation journal — port of
/// <c>app/infrastructure/encryption/rotation_journal.py</c>.
/// </summary>
/// <remarks>
/// Rotation re-encrypts every object under a new layer key, then rewraps the
/// header. If the process dies in the middle, some objects are under the new key
/// and the header still unwraps the old one. The journal stores the new key wrapped
/// with the old key (AEAD, same primitive as objects) plus the object ids already
/// rewritten, so the next unlock or rotate can finish the job.
/// <para>
/// The journal is ciphertext. It is useless without the old layer key, which still
/// comes from the password. It is deleted after the new header is committed.
/// </para>
/// </remarks>
public sealed class RotationJournal
{
    public const string JournalName = "rotation.journal";

    public static ReadOnlySpan<byte> Aad => "strata-rotation-journal-v1"u8;

    public RotationJournal(string root)
    {
        Path = System.IO.Path.Combine(root, JournalName);
    }

    public string Path { get; }

    private string TemporaryPath => Path + ".tmp";

    public bool Exists() => File.Exists(Path);

    public void Save(
        string layerId,
        string manifestObjectId,
        bool paddingEnabled,
        IReadOnlyList<string> doneObjectIds,
        ReadOnlySpan<byte> oldKey,
        ReadOnlySpan<byte> newKey)
    {
        var (nonce, wrapped) = AeadPrimitives.Encrypt(oldKey, newKey, Aad);
        var done = new JsonArray();
        foreach (var id in doneObjectIds)
        {
            done.Add(id);
        }

        var payload = new JsonObject
        {
            ["v"] = 1,
            ["layer_id"] = layerId,
            ["manifest_object_id"] = manifestObjectId,
            ["padding_enabled"] = paddingEnabled,
            ["done_object_ids"] = done,
            ["nonce"] = Convert.ToHexString(nonce).ToLowerInvariant(),
            ["wrapped_new_key"] = Convert.ToHexString(wrapped).ToLowerInvariant(),
        };

        AtomicFile.WriteText(Path, payload.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
    }

    public RotationJournalState Load(ReadOnlySpan<byte> oldKey)
    {
        JsonNode? node;
        try
        {
            node = JsonNode.Parse(File.ReadAllText(Path));
        }
        catch (Exception exc) when (exc is JsonException or IOException or UnauthorizedAccessException)
        {
            throw new DecryptionException("The rotation journal could not be read.", exc);
        }

        if (node is not JsonObject raw)
        {
            throw new DecryptionException("The rotation journal is not valid.");
        }

        byte[] newKey;
        try
        {
            newKey = AeadPrimitives.Decrypt(
                oldKey,
                Convert.FromHexString(raw["nonce"]!.GetValue<string>()),
                Convert.FromHexString(raw["wrapped_new_key"]!.GetValue<string>()),
                Aad);
        }
        catch (Exception exc) when (exc is NullReferenceException or InvalidOperationException
            or FormatException or ArgumentException)
        {
            throw new DecryptionException("The rotation journal is not valid.", exc);
        }

        var done = raw["done_object_ids"] is JsonArray array
            ? array.Select(item => item?.GetValue<string>() ?? string.Empty).ToList()
            : [];

        return new RotationJournalState(
            LayerId: raw["layer_id"]?.GetValue<string>() ?? string.Empty,
            ManifestObjectId: raw["manifest_object_id"]?.GetValue<string>() ?? string.Empty,
            PaddingEnabled: raw["padding_enabled"]?.GetValue<bool>() ?? true,
            DoneObjectIds: done,
            NewKey: newKey);
    }

    public void Clear()
    {
        AtomicFile.TryDelete(Path);
        AtomicFile.TryDelete(TemporaryPath);
    }
}
