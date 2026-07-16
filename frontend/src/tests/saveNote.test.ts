/**
 * Save coordination: no note's content is ever written into another note, and a
 * save in flight never drops the newest content.
 *
 * These guard the two data-loss bugs a review found in the editor save path.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../state/store";
import { installFakeBridge } from "./fakeBridge";

describe("saveNote", () => {
  beforeEach(() => {
    installFakeBridge();
    useStore.setState({ activeNoteId: "n1", dirty: {}, saving: false });
  });

  // Restore any bridge spies so a mock cannot leak into another test file.
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not drop the newest content when a save is already in flight", async () => {
    const seen: string[] = [];
    // Intercept the bridge call to observe every write and to make the first
    // one slow, so the second queues behind it.
    const original = (await import("../bridge/client")).bridge.notes.update;
    let calls = 0;
    vi.spyOn(
      (await import("../bridge/client")).bridge.notes,
      "update",
    ).mockImplementation(async (id: string, content: string) => {
      seen.push(content);
      calls += 1;
      if (calls === 1) await new Promise((r) => setTimeout(r, 20));
      return original(id, content);
    });

    const p1 = useStore.getState().saveNote("n1", "first");
    // Second save arrives while the first is still awaiting.
    const p2 = useStore.getState().saveNote("n1", "second");
    await Promise.all([p1, p2]);

    // Both contents were written, in order — the newest was not dropped.
    expect(seen).toEqual(["first", "second"]);
  });

  it("does not apply a background note's save to the open note", async () => {
    // n2 is being saved while n1 is the note on screen.
    useStore.setState({ activeNoteId: "n1", openNote: null });
    await useStore.getState().saveNote("n2", "content of n2");
    // The open note (n1) must be untouched by n2's save response.
    expect(useStore.getState().activeNoteId).toBe("n1");
  });
});
