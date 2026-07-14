/**
 * The editor: autosave, tabs, and the external-change conflict.
 *
 * The behaviour that matters most here is the one nobody notices until it fails:
 * an edit is never lost, and a file changed on disk is never silently clobbered.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { EditorPane } from "../features/editor/EditorPane";
import { useStore } from "../state/store";
import {
  emitChanged,
  installFakeBridge,
  saved,
  SAMPLE_GRAPH,
} from "./fakeBridge";

function reset(): void {
  useStore.setState({
    connection: "ready",
    graph: SAMPLE_GRAPH,
    openNote: null,
    activeNoteId: null,
    tabs: [],
    draft: null,
    dirty: {},
    saving: false,
    externalChange: false,
    viewMode: "source",
    tree: null,
    schemas: [],
    links: { backlinks: [], unlinked_mentions: [], outgoing: [] },
  });
}

describe("EditorPane", () => {
  beforeEach(() => {
    installFakeBridge();
    reset();
  });

  it("prompts to create or open a note when nothing is open", () => {
    render(<EditorPane />);

    expect(screen.getByText(/Select a note in the tree/i)).toBeInTheDocument();
  });

  it("opens a note into a tab and shows its title", async () => {
    await useStore.getState().openNoteById("n1");

    render(<EditorPane />);

    expect(
      screen.getByRole("tab", { name: /Encryption Architecture/ }),
    ).toBeInTheDocument();
    expect(useStore.getState().tabs).toHaveLength(1);
  });

  it("keeps one tab per note, not one per open", async () => {
    await useStore.getState().openNoteById("n1");
    await useStore.getState().openNoteById("n1");
    await useStore.getState().openNoteById("n2");

    expect(useStore.getState().tabs.map((tab) => tab.id)).toEqual(["n1", "n2"]);
  });

  it("marks a tab dirty while there are unsaved edits", async () => {
    await useStore.getState().openNoteById("n1");

    act(() => {
      useStore.getState().setDraft("n1", "changed body");
    });

    expect(useStore.getState().dirty["n1"]).toBe(true);

    await act(async () => {
      await useStore.getState().saveNote("n1", "changed body");
    });

    expect(useStore.getState().dirty["n1"]).toBe(false);
    expect(saved).toContain("changed body");
  });

  it("does not mark a note dirty when the text is unchanged", async () => {
    await useStore.getState().openNoteById("n1");
    const original = useStore.getState().openNote!.content;

    act(() => {
      useStore.getState().setDraft("n1", original);
    });

    expect(useStore.getState().dirty["n1"]).toBeFalsy();
  });

  it("closing a tab activates the previous one", async () => {
    await useStore.getState().openNoteById("n1");
    await useStore.getState().openNoteById("n2");

    await act(async () => {
      useStore.getState().closeTab("n2");
      await Promise.resolve(); // closeTab reopens the previous tab asynchronously
    });

    await waitFor(() => {
      expect(useStore.getState().activeNoteId).toBe("n1");
    });
  });

  it("closing the last tab clears the editor", async () => {
    await useStore.getState().openNoteById("n1");

    act(() => {
      useStore.getState().closeTab("n1");
    });

    expect(useStore.getState().openNote).toBeNull();
    expect(useStore.getState().activeNoteId).toBeNull();
  });

  it("switching notes never carries a draft across", async () => {
    await useStore.getState().openNoteById("n1");
    act(() => {
      useStore.getState().setDraft("n1", "n1 draft");
    });

    await useStore.getState().openNoteById("n2");

    // A draft that survived the switch would be written into the wrong file.
    expect(useStore.getState().draft).toBeNull();
  });

  it("an external change with no unsaved edits reloads silently", async () => {
    await useStore.getState().openNoteById("n1");
    await useStore.getState().initialise();

    await act(async () => {
      emitChanged("external");
      await Promise.resolve(); // let the listener's async work settle
    });

    await waitFor(() => {
      expect(useStore.getState().externalChange).toBe(false);
    });
  });

  it("an external change with unsaved edits asks instead of clobbering", async () => {
    await useStore.getState().initialise();
    await useStore.getState().openNoteById("n1");

    act(() => {
      useStore.getState().setDraft("n1", "my precious unsaved words");
    });

    await act(async () => {
      emitChanged("external");
      await Promise.resolve(); // let the listener's async work settle
    });

    await waitFor(() => {
      expect(useStore.getState().externalChange).toBe(true);
    });

    render(<EditorPane />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      /changed on disk outside Strata/i,
    );
    expect(
      screen.getByRole("button", { name: /Reload from disk/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Keep my edits/i }),
    ).toBeInTheDocument();

    // The draft is still intact: nothing was overwritten while we asked.
    expect(useStore.getState().draft).toBe("my precious unsaved words");
  });

  it("keeping local edits saves them and clears the conflict", async () => {
    const user = userEvent.setup();
    await useStore.getState().initialise();
    await useStore.getState().openNoteById("n1");

    act(() => {
      useStore.getState().setDraft("n1", "keep these");
      useStore.setState({ externalChange: true });
    });

    render(<EditorPane />);
    await user.click(screen.getByRole("button", { name: /Keep my edits/i }));

    await waitFor(() => {
      expect(saved).toContain("keep these");
    });
    expect(useStore.getState().externalChange).toBe(false);
  });

  it("reloading from disk discards the draft", async () => {
    const user = userEvent.setup();
    await useStore.getState().initialise();
    await useStore.getState().openNoteById("n1");

    act(() => {
      useStore.getState().setDraft("n1", "discard me");
      useStore.setState({ externalChange: true });
    });

    render(<EditorPane />);
    await user.click(screen.getByRole("button", { name: /Reload from disk/i }));

    await waitFor(() => {
      expect(useStore.getState().draft).toBeNull();
    });
    expect(saved).not.toContain("discard me");
    expect(useStore.getState().externalChange).toBe(false);
  });

  it("renaming a note follows the new id into the tab", async () => {
    await useStore.getState().openNoteById("n1");

    await act(async () => {
      await useStore.getState().renameNote("n1", "New Name");
    });

    // The id is derived from the path, so a rename changes it; a tab left pointing
    // at the old id would open a file that no longer exists.
    expect(useStore.getState().tabs[0]!.id).toBe("n1-renamed");
    expect(useStore.getState().activeNoteId).toBe("n1-renamed");
  });

  it("shows the three view modes and switches between them", async () => {
    const user = userEvent.setup();
    await useStore.getState().openNoteById("n1");

    render(<EditorPane />);
    await user.click(screen.getByRole("button", { name: "Reading" }));

    expect(useStore.getState().viewMode).toBe("reading");
    expect(screen.getByTestId("markdown-preview")).toBeInTheDocument();
    expect(screen.queryByTestId("markdown-editor")).not.toBeInTheDocument();
  });
});
