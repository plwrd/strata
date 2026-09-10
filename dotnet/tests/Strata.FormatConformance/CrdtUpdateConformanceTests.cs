using System.Text.Json;
using Strata.Core.Errors;
using Strata.Infrastructure.Crdt;
using Strata.Infrastructure.Encryption;

namespace Strata.FormatConformance;

/// <summary>
/// Sealed Yjs updates. The vector is the same pycrdt/Yjs update the frontend interop
/// test uses, so this pins the whole chain: Yjs bytes → content-derived id → container.
/// </summary>
public class CrdtUpdateConformanceTests
{
    private static readonly JsonElement Vector = Fixtures.Json("crdt_update.json");

    private static byte[] Update => Convert.FromBase64String(Vector.GetProperty("yjs_update_b64").GetString()!);

    private static byte[] Blob => Fixtures.Bytes(Vector.GetProperty("blob_file").GetString()!);

    private static string DocId => Vector.GetProperty("doc_id").GetString()!;

    private static string LayerId => Vector.GetProperty("layer_id").GetString()!;

    [Fact]
    public void Object_id_is_blake2b_128_of_doc_id_nul_and_update()
        => Assert.Equal(Vector.Hex("object_id_hex"), UpdateSealing.UpdateObjectId(DocId, Update));

    [Fact]
    public void SealUpdate_reproduces_the_python_blob_byte_for_byte()
        => Assert.Equal(
            Blob,
            UpdateSealing.SealUpdate(Vector.Hex("key_hex"), LayerId, DocId, Update, nonce: Vector.Hex("nonce_hex")));

    [Fact]
    public void OpenUpdate_recovers_the_yjs_bytes()
        => Assert.Equal(Update, UpdateSealing.OpenUpdate(Vector.Hex("key_hex"), LayerId, DocId, Blob));

    [Fact]
    public void The_blob_is_typed_as_a_crdt_update()
        => Assert.Equal(ObjectTypes.CrdtUpdate, ObjectHeader.Unpack(Blob).ObjectType);

    [Fact]
    public void An_update_from_another_document_is_refused()
        => Assert.Throws<DecryptionException>(() => UpdateSealing.OpenUpdate(
            Vector.Hex("key_hex"),
            LayerId,
            Vector.GetProperty("negatives").GetProperty("wrong_doc_id").GetString()!,
            Blob));

    [Fact]
    public void An_update_from_another_layer_is_refused()
        => Assert.Throws<DecryptionException>(() => UpdateSealing.OpenUpdate(
            Vector.Hex("key_hex"),
            Vector.GetProperty("negatives").GetProperty("wrong_layer_id").GetString()!,
            DocId,
            Blob));

    [Fact]
    public void An_update_cannot_be_opened_as_a_compacted_state()
        => Assert.Throws<DecryptionException>(
            () => UpdateSealing.OpenUpdate(Vector.Hex("key_hex"), LayerId, DocId, Blob, isState: true));

    [Fact]
    public void A_tampered_update_body_is_refused()
    {
        var tampered = Blob.ToArray();
        tampered[^1] ^= 0x01;

        Assert.Throws<DecryptionException>(
            () => UpdateSealing.OpenUpdate(Vector.Hex("key_hex"), LayerId, DocId, tampered));
    }

    [Fact]
    public void Compacted_states_roundtrip_under_their_own_type()
    {
        var key = AeadPrimitives.RandomKey();
        var state = UpdateSealing.SealUpdate(key, LayerId, DocId, Update, isState: true);

        Assert.Equal(ObjectTypes.CrdtState, ObjectHeader.Unpack(state).ObjectType);
        Assert.Equal(Update, UpdateSealing.OpenUpdate(key, LayerId, DocId, state, isState: true));
        Assert.Throws<DecryptionException>(() => UpdateSealing.OpenUpdate(key, LayerId, DocId, state));
    }

    [Fact]
    public void The_same_update_in_two_documents_gets_two_ids()
        => Assert.NotEqual(
            UpdateSealing.UpdateObjectId("doc-a", Update),
            UpdateSealing.UpdateObjectId("doc-b", Update));

    [Fact]
    public void The_nul_separator_stops_doc_id_and_update_from_running_together()
    {
        // Without the separator, ("ab", "c") and ("a", "bc") would collide.
        Assert.NotEqual(
            UpdateSealing.UpdateObjectId("ab", "c"u8),
            UpdateSealing.UpdateObjectId("a", "bc"u8));
    }
}
