/**
 * Collaboration (M9): share a layer, sync it, and resolve conflicts.
 *
 * Everything here is a thin control over the Python service, which is the
 * authority: it holds the CRDT, seals every update, and enforces roles. This
 * panel only shows state and issues intents. A locked layer never appears —
 * `require_readable_layer` gates the service, so a locked layer reports nothing.
 *
 * The conflict list is the product's promise made visible: when a merge would
 * have lost data, the data is instead rescued into Conflicts/ and shown here to
 * be kept or, deliberately, confirmed for deletion.
 */

import { useEffect, useState } from "react";
import type { LayerDescriptor } from "../../bridge/types";
import { useStore } from "../../state/store";

function isReadable(layer: LayerDescriptor): boolean {
  return layer.state === "mounted" || layer.state === "unlocked";
}

export function CollaborationPanel(): JSX.Element | null {
  const layers = useStore((s) => s.layers);
  const collab = useStore((s) => s.collab);
  const conflicts = useStore((s) => s.collabConflicts);
  const loadCollab = useStore((s) => s.loadCollab);

  const readable = layers.filter(isReadable);

  useEffect(() => {
    for (const layer of readable) void loadCollab(layer.id);
    // Re-check whenever the set of readable layers changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readable.map((l) => `${l.id}:${l.state}`).join(",")]);

  if (readable.length === 0) return null;

  return (
    <section className="panel collab" aria-label="Collaboration">
      <h2 className="panel__title">Collaboration</h2>
      <RelayConfig />
      <ul className="collab__list">
        {readable.map((layer) => (
          <CollabRow
            key={layer.id}
            layer={layer}
            state={collab[layer.id]}
            conflicts={conflicts[layer.id] ?? []}
          />
        ))}
      </ul>
    </section>
  );
}

/** Choose where collaboration syncs: a local shared folder, or a network relay. */
function RelayConfig(): JSX.Element {
  const settings = useStore((s) => s.settings);
  const applySettings = useStore((s) => s.applySettings);
  const current = settings?.relay_url ?? "";
  const [value, setValue] = useState(current);
  const [saved, setSaved] = useState(false);
  const [touched, setTouched] = useState(false);

  // Settings may load after this panel mounts. Adopt the stored value until the
  // user actually edits the field — otherwise the input stays empty while the
  // real relay URL loads, and one Save would wipe the configured relay.
  useEffect(() => {
    if (!touched) setValue(current);
  }, [current, touched]);

  const dirty = value.trim() !== current;

  return (
    <form
      className="collab__relay"
      onSubmit={(e) => {
        e.preventDefault();
        void applySettings({ relay_url: value.trim() }).then(() =>
          setSaved(true),
        );
      }}
    >
      <label className="collab__relay-label" htmlFor="relay-url">
        Sync relay {current ? "(network)" : "(local folder)"}
      </label>
      <div className="collab__relay-row">
        <input
          id="relay-url"
          className="input input--small"
          placeholder="https://relay.example/ — blank for local"
          value={value}
          onChange={(e) => {
            setTouched(true);
            setValue(e.target.value);
            setSaved(false);
          }}
        />
        <button
          type="submit"
          className="button button--small"
          disabled={!dirty}
        >
          Save
        </button>
      </div>
      <p
        className="collab__relay-hint mono"
        title="A relay forwards encrypted blobs only — never plaintext."
      >
        {saved
          ? "Saved for new sessions (ciphertext only)."
          : "Relay sees ciphertext only."}
      </p>
    </form>
  );
}

function CollabRow({
  layer,
  state,
  conflicts,
}: {
  layer: LayerDescriptor;
  state: ReturnType<typeof useStore.getState>["collab"][string] | undefined;
  conflicts: ReturnType<typeof useStore.getState>["collabConflicts"][string];
}): JSX.Element {
  const shareLayer = useStore((s) => s.shareLayer);
  const joinLayer = useStore((s) => s.joinLayer);
  const leaveCollab = useStore((s) => s.leaveCollab);
  const syncCollab = useStore((s) => s.syncCollab);
  const resolveConflict = useStore((s) => s.resolveConflict);

  const [busy, setBusy] = useState(false);
  const [joining, setJoining] = useState(false);
  const [joinDoc, setJoinDoc] = useState("");
  const [error, setError] = useState<string | null>(null);

  const shared = state?.enabled ?? false;

  const run = async (fn: () => Promise<void>): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="collab__row">
      <div className="collab__head">
        <span
          className="collab__name"
          style={{ color: `var(--${layer.color})` }}
        >
          {layer.display_name}
        </span>
        <span className={`collab__badge ${shared ? "collab__badge--on" : ""}`}>
          {shared ? "shared" : "personal"}
        </span>
      </div>

      {!shared && (
        <div className="collab__actions">
          <button
            type="button"
            className="button button--small"
            disabled={busy}
            onClick={() => void run(() => shareLayer(layer.id))}
          >
            Share this layer
          </button>
          <button
            type="button"
            className="button button--small button--ghost"
            disabled={busy}
            onClick={() => setJoining((v) => !v)}
          >
            Join…
          </button>
        </div>
      )}

      {!shared && joining && (
        <form
          className="collab__join"
          onSubmit={(e) => {
            e.preventDefault();
            if (joinDoc.trim())
              void run(() => joinLayer(layer.id, joinDoc.trim())).then(() =>
                setJoining(false),
              );
          }}
        >
          <input
            className="input input--small"
            placeholder="document id from an invite"
            value={joinDoc}
            onChange={(e) => setJoinDoc(e.target.value)}
            aria-label="Document id to join"
          />
          <button
            type="submit"
            className="button button--small"
            disabled={busy}
          >
            Join
          </button>
        </form>
      )}

      {shared && state && (
        <>
          <dl className="collab__meta">
            <div>
              <dt>Invite (document id)</dt>
              <dd className="mono collab__docid">{state.doc_id}</dd>
            </div>
            <div>
              <dt>Role</dt>
              <dd>{state.role}</dd>
            </div>
            <div>
              <dt>Peers</dt>
              <dd>{state.peers.length}</dd>
            </div>
          </dl>

          {state.peers.length > 0 && (
            <ul className="collab__peers">
              {state.peers.map((p) => (
                <li key={p.peer_id} className="collab__peer">
                  <span
                    className="collab__peer-dot"
                    style={{ background: `var(--${p.color})` }}
                    aria-hidden="true"
                  />
                  {p.display_name}
                </li>
              ))}
            </ul>
          )}

          <div className="collab__actions">
            <button
              type="button"
              className="button button--small"
              disabled={busy}
              onClick={() => void run(() => syncCollab(layer.id))}
            >
              Sync now
            </button>
            <button
              type="button"
              className="button button--small button--ghost"
              disabled={busy}
              onClick={() => void run(() => leaveCollab(layer.id))}
            >
              Leave
            </button>
          </div>
        </>
      )}

      {conflicts.length > 0 && (
        <div className="collab__conflicts" role="alert">
          <p className="collab__conflicts-title">
            {conflicts.length} conflict{conflicts.length > 1 ? "s" : ""} —
            nothing was lost
          </p>
          {conflicts.map((c) => (
            <div key={c.conflict_id} className="collab__conflict">
              <p className="collab__conflict-text">{c.summary}</p>
              <div className="collab__actions">
                <button
                  type="button"
                  className="button button--small"
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      resolveConflict(layer.id, c.conflict_id, "keep"),
                    )
                  }
                >
                  Keep in Conflicts/
                </button>
                <button
                  type="button"
                  className="button button--small button--danger"
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      resolveConflict(
                        layer.id,
                        c.conflict_id,
                        "confirm_delete",
                      ),
                    )
                  }
                >
                  Confirm delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {error && <p className="collab__error">{error}</p>}
    </li>
  );
}
