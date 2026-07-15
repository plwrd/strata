/**
 * A client-side Yjs session bound to Python's authoritative document.
 *
 * ADR-0006 puts the authoritative CRDT in Python (only it holds the key). The
 * renderer holds a *view*: a client `Y.Doc` that gives the editor sub-frame
 * responsiveness, kept in sync with Python over the bridge using the same Yjs
 * binary updates both runtimes speak (proven by the interop test).
 *
 * The two directions:
 * - **Local → Python.** Every local transaction emits a Yjs update; we forward
 *   it to `collaboration.apply_update`. Python merges, seals, persists, relays.
 * - **Python → local.** On a `changed` event (a peer's edit, or a conflict
 *   rescue) we pull the authoritative state and apply it with the `"remote"`
 *   origin — which our own update listener ignores, so nothing echoes back.
 *
 * Applying full state is idempotent in Yjs, so "pull the whole document" is
 * correct if wasteful; it keeps the protocol trivially convergent.
 */

import { Awareness } from "y-protocols/awareness";
import * as Y from "yjs";
import { bridge } from "../../bridge/client";

const REMOTE = "remote";

function decode(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function encode(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1)
    binary += String.fromCharCode(bytes[i]!);
  return btoa(binary);
}

export interface CollabEvent {
  kind: string;
  layerId?: string;
  pending?: number;
}

export class CollabSession {
  readonly doc = new Y.Doc();
  readonly awareness = new Awareness(this.doc);

  private applyingRemote = false;
  private connected = false;
  private disposed = false;

  constructor(readonly layerId: string) {}

  /** Load the authoritative state and start bidirectional sync. */
  async connect(): Promise<void> {
    if (this.connected || this.disposed) return;
    const { update } = await bridge.collaboration.getDocument(this.layerId);
    this.applyRemote(decode(update));
    this.doc.on("update", this.handleLocalUpdate);
    await bridge.collaboration.onEvent(this.handleEvent);
    this.connected = true;
  }

  /** The `Y.Text` for a note body — the exact object the editor binds to. */
  text(noteId: string): Y.Text {
    const bodies = this.doc.getMap("bodies");
    const existing = bodies.get(noteId);
    if (existing instanceof Y.Text) return existing;
    const created = new Y.Text();
    bodies.set(noteId, created);
    return created;
  }

  /** Pull the authoritative state (called on remote change). */
  async pull(): Promise<void> {
    if (this.disposed) return;
    const { update } = await bridge.collaboration.getDocument(this.layerId);
    this.applyRemote(decode(update));
  }

  destroy(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.doc.off("update", this.handleLocalUpdate);
    this.awareness.destroy();
    this.doc.destroy();
  }

  // -- internals ----------------------------------------------------------

  private handleLocalUpdate = (update: Uint8Array, origin: unknown): void => {
    // Remote-originated updates must not be sent back — that is the echo loop.
    if (origin === REMOTE || this.applyingRemote || this.disposed) return;
    void bridge.collaboration.applyUpdate(this.layerId, encode(update));
  };

  private applyRemote(update: Uint8Array): void {
    this.applyingRemote = true;
    try {
      Y.applyUpdate(this.doc, update, REMOTE);
    } finally {
      this.applyingRemote = false;
    }
  }

  private handleEvent = (payload: string): void => {
    if (this.disposed) return;
    let event: CollabEvent;
    try {
      event = JSON.parse(payload) as CollabEvent;
    } catch {
      return;
    }
    if (event.kind === "changed" && event.layerId === this.layerId) {
      void this.pull();
    }
  };
}

// One session per shared layer, created lazily and reused across note switches.
// Sessions are stateful Y.Docs — deliberately not in the Zustand store, which is
// for serialisable view state.
const sessions = new Map<string, CollabSession>();
const connecting = new Map<string, Promise<CollabSession>>();

/** Get (or create and connect) the session for a shared layer. */
export async function sessionFor(layerId: string): Promise<CollabSession> {
  const existing = sessions.get(layerId);
  if (existing) return existing;
  const pending = connecting.get(layerId);
  if (pending) return pending;

  const session = new CollabSession(layerId);
  const promise = session.connect().then(() => {
    sessions.set(layerId, session);
    connecting.delete(layerId);
    return session;
  });
  connecting.set(layerId, promise);
  return promise;
}

/** Drop a session (e.g. when the layer stops being shared). */
export function dropSession(layerId: string): void {
  sessions.get(layerId)?.destroy();
  sessions.delete(layerId);
  connecting.delete(layerId);
}

/** Tear down every session (workspace close, tests). */
export function resetSessions(): void {
  for (const session of sessions.values()) session.destroy();
  sessions.clear();
  connecting.clear();
}
