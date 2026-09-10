using System.Text;
using Strata.Core.Errors;
using Strata.Infrastructure.Encryption;

namespace Strata.Infrastructure.Crdt;

/// <summary>
/// Sealing Yjs updates for storage and for the relay — port of
/// <c>app/infrastructure/crdt/updates.py</c>.
/// </summary>
/// <remarks>
/// The sealed object id is <b>content-derived</b>: <c>blake2b(doc_id || 0x00 || update)</c>.
/// It binds the update to its document (a blob from another doc fails to open) and to
/// its own bytes (tampering fails authentication), and — crucially — it does not
/// depend on the update's position in the relay log. An earlier design derived the id
/// from the relay sequence number; the sealer cannot know the sequence the relay will
/// assign until after publishing, so under concurrent publishers the sealed id and the
/// stored position diverged and every peer silently dropped the update.
/// </remarks>
public static class UpdateSealing
{
    /// <summary>A 16-byte id binding an update to its document and its exact content.</summary>
    public static byte[] UpdateObjectId(string docId, ReadOnlySpan<byte> update)
    {
        var docBytes = Encoding.UTF8.GetBytes(docId);
        var material = new byte[docBytes.Length + 1 + update.Length];
        docBytes.CopyTo(material.AsSpan());
        material[docBytes.Length] = 0;
        update.CopyTo(material.AsSpan(docBytes.Length + 1));
        return Blake2b.Hash(material, 16);
    }

    /// <summary>Seal a Yjs update (or a compacted base state) into a storable blob.</summary>
    public static byte[] SealUpdate(
        ReadOnlySpan<byte> key,
        string layerId,
        string docId,
        ReadOnlySpan<byte> update,
        bool isState = false,
        ReadOnlySpan<byte> nonce = default)
        => ObjectContainer.Seal(
            key,
            layerId,
            UpdateObjectId(docId, update),
            isState ? ObjectTypes.CrdtState : ObjectTypes.CrdtUpdate,
            update,
            pad: true,
            nonce: nonce);

    /// <summary>Verify and decrypt a sealed update. Any tampering is a <see cref="DecryptionException"/>.</summary>
    public static byte[] OpenUpdate(
        ReadOnlySpan<byte> key,
        string layerId,
        string docId,
        ReadOnlySpan<byte> blob,
        bool isState = false)
    {
        // The object id lives in the (AAD-authenticated) header, so the reader need
        // not know the relay position to open the blob.
        var header = ObjectHeader.Unpack(blob);
        var plaintext = ObjectContainer.OpenSealed(
            key,
            layerId,
            header.ObjectId,
            isState ? ObjectTypes.CrdtState : ObjectTypes.CrdtUpdate,
            blob);

        // Bind the id to the content: a blob whose header id does not match its own
        // bytes (or belongs to another document) is a tamper, not an update.
        if (!AeadPrimitives.ConstantTimeEquals(header.ObjectId, UpdateObjectId(docId, plaintext)))
        {
            throw new DecryptionException("The update object id does not bind its content.");
        }

        return plaintext;
    }
}
