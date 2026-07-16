/**
 * The client collaborative session: bidirectional Yjs sync over the bridge.
 *
 * The fake bridge holds a real Yjs authority per layer, so these tests exercise
 * the actual protocol — load, local-edit-forwards, remote-change-pulls, and the
 * echo guard — not a mock of it.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { CollabSession } from "../features/collaboration/collabDoc";
import { emitCollabEvent, installFakeBridge } from "./fakeBridge";

describe("CollabSession", () => {
  beforeEach(() => {
    installFakeBridge();
  });

  it("loads the authoritative document on connect", async () => {
    // Author into the fake authority through one session, then load in another.
    const author = new CollabSession("L");
    await author.connect();
    author.text("n1").insert(0, "shared body");
    // give the forwarded update a tick to land in the fake authority
    await Promise.resolve();

    const reader = new CollabSession("L");
    await reader.connect();
    expect(reader.text("n1").toJSON()).toBe("shared body");

    author.destroy();
    reader.destroy();
  });

  it("forwards a local edit to Python", async () => {
    const session = new CollabSession("L");
    await session.connect();

    session.text("n1").insert(0, "typed");
    await Promise.resolve();

    // A second session loading fresh sees the forwarded edit — proving it reached
    // the authority, not just the local doc.
    const other = new CollabSession("L");
    await other.connect();
    expect(other.text("n1").toJSON()).toBe("typed");

    session.destroy();
    other.destroy();
  });

  it("pulls remote changes on a changed event", async () => {
    const a = new CollabSession("L");
    const b = new CollabSession("L");
    await a.connect();
    await b.connect();

    // A writes; B has not pulled yet (and must not pre-bind n1 before it exists).
    a.text("n1").insert(0, "from A");
    await Promise.resolve();

    // Python signals the change; B pulls and converges.
    emitCollabEvent({ kind: "changed", layerId: "L" });
    await new Promise((r) => setTimeout(r, 0));
    expect(b.text("n1").toJSON()).toBe("from A");

    a.destroy();
    b.destroy();
  });

  it("does not echo a pulled remote update back to Python", async () => {
    const a = new CollabSession("L");
    await a.connect();
    a.text("n1").insert(0, "content");
    await Promise.resolve();

    const b = new CollabSession("L");
    await b.connect();
    const spy = vi.spyOn(
      (await import("../bridge/client")).bridge.collaboration,
      "applyUpdate",
    );

    emitCollabEvent({ kind: "changed", layerId: "L" });
    await new Promise((r) => setTimeout(r, 0));

    // Applying the pulled state must not trigger an apply_update call.
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
    a.destroy();
    b.destroy();
  });

  it("ignores events for other layers", async () => {
    const session = new CollabSession("L");
    await session.connect();
    const pull = vi.spyOn(session, "pull");

    emitCollabEvent({ kind: "changed", layerId: "OTHER" });
    await new Promise((r) => setTimeout(r, 0));

    expect(pull).not.toHaveBeenCalled();
    pull.mockRestore();
    session.destroy();
  });
});

describe("session registry resilience", () => {
  beforeEach(() => {
    installFakeBridge();
  });

  it("does not cache a failed connect forever", async () => {
    const client = await import("../bridge/client");
    const { sessionFor, resetSessions } =
      await import("../features/collaboration/collabDoc");
    resetSessions();

    const spy = vi
      .spyOn(client.bridge.collaboration, "getDocument")
      .mockRejectedValueOnce(new Error("transient"));

    await expect(sessionFor("layerX")).rejects.toThrow();

    // A second attempt must retry (the fake now succeeds), not replay the
    // cached rejection.
    spy.mockRestore();
    const session = await sessionFor("layerX");
    expect(session).toBeTruthy();
    resetSessions();
  });
});
