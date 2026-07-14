/**
 * Private layers in the UI.
 *
 * The load-bearing test here is the last one: **locking must purge the frontend
 * too.** Python forgetting the key is necessary but not sufficient — the open tab,
 * the draft being typed, the search results on screen and the graph selection are
 * all decrypted content living in the renderer process. If locking leaves them
 * there, "locked" is a lie the UI is telling the user.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LayerPanel } from "../features/layers/LayerPanel";
import { useStore } from "../state/store";
import { installFakeBridge, PRIVATE_LAYER, PUBLIC_LAYER } from "./fakeBridge";
import { stubClipboard } from "./setup";

function seed(privateState: "locked" | "unlocked" = "locked"): void {
  useStore.setState({
    connection: "ready",
    layers: [PUBLIC_LAYER, { ...PRIVATE_LAYER, state: privateState }],
    tabs: [],
    dirty: {},
    selectedIds: [],
    searchResults: [],
    openNote: null,
    activeNoteId: null,
    draft: null,
    tree: null,
  });
}

describe("LayerPanel", () => {
  beforeEach(() => {
    installFakeBridge();
    seed();
  });

  it("shows a locked private layer as locked", () => {
    render(<LayerPanel />);

    expect(screen.getByText("locked")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unlock" })).toBeInTheDocument();
  });

  it("says what a locked layer contributes: nothing", () => {
    render(<LayerPanel />);

    expect(screen.getByText(/contributes nothing/i)).toBeInTheDocument();
  });

  it("unlocks with a password and never keeps it in the store", async () => {
    const user = userEvent.setup();
    render(<LayerPanel />);

    await user.click(screen.getByRole("button", { name: "Unlock" }));
    await user.type(
      screen.getByLabelText("Layer password"),
      "correct horse battery",
    );
    // Two buttons are now named "Unlock": the row's and the dialog's submit.
    await user.click(screen.getAllByRole("button", { name: "Unlock" })[1]!);

    await waitFor(() => {
      expect(useStore.getState().layers[1]!.state).toBe("unlocked");
    });

    // The password must not be anywhere in the application state.
    const state = JSON.stringify(useStore.getState());
    expect(state).not.toContain("correct horse battery");
  });

  it("reports a failed unlock without saying why it failed", async () => {
    const user = userEvent.setup();
    installFakeBridge({
      failWith: {
        code: "internal",
        message: "The data could not be decrypted.",
      },
    });
    seed();

    render(<LayerPanel />);
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    await user.type(screen.getByLabelText("Layer password"), "wrong");
    await user.click(screen.getAllByRole("button", { name: "Unlock" })[1]!);

    const alert = await screen.findByRole("alert");
    // No "wrong password" — that would tell an attacker the layer exists and is
    // guessable, and tells the owner nothing they did not know.
    expect(alert).toHaveTextContent(/did not unlock/i);
    expect(alert.textContent?.toLowerCase()).not.toContain("wrong password");
    expect(alert.textContent?.toLowerCase()).not.toContain("corrupt");
  });

  it("creating a private layer requires a password and warns there is no reset", async () => {
    const user = userEvent.setup();
    render(<LayerPanel />);

    await user.click(screen.getByTitle("New layer"));
    await user.type(screen.getByLabelText("Name"), "Research");
    await user.click(screen.getByRole("radio", { name: /Private/ }));

    expect(screen.getByText(/No reset/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Create encrypted layer/ }),
    ).toBeDisabled();

    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.type(
      screen.getByLabelText("Confirm password"),
      "correct horse battery",
    );

    expect(
      screen.getByRole("button", { name: /Create encrypted layer/ }),
    ).toBeEnabled();
  });

  it("a mismatched confirmation blocks creation", async () => {
    const user = userEvent.setup();
    render(<LayerPanel />);

    await user.click(screen.getByTitle("New layer"));
    await user.type(screen.getByLabelText("Name"), "Research");
    await user.click(screen.getByRole("radio", { name: /Private/ }));
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.type(
      screen.getByLabelText("Confirm password"),
      "different entirely",
    );

    expect(
      screen.getByRole("button", { name: /Create encrypted layer/ }),
    ).toBeDisabled();
  });

  it("shows the recovery key once and will not let it be dismissed unacknowledged", async () => {
    const user = userEvent.setup();
    stubClipboard(vi.fn().mockResolvedValue(undefined));
    render(<LayerPanel />);

    await user.click(screen.getByTitle("New layer"));
    await user.type(screen.getByLabelText("Name"), "Research");
    await user.click(screen.getByRole("radio", { name: /Private/ }));
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.type(
      screen.getByLabelText("Confirm password"),
      "correct horse battery",
    );
    await user.click(
      screen.getByRole("button", { name: /Create encrypted layer/ }),
    );

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/Shown once/i);
    expect(dialog).toHaveTextContent(/There is no second copy/i);
    expect(screen.getByTestId("recovery-key")).toHaveTextContent("AAAA-BBBB");

    // The user cannot click past it until they say they saved it.
    expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", { name: /saved this recovery key/i }),
    );
    expect(screen.getByRole("button", { name: "Done" })).toBeEnabled();
  });
});

describe("locking purges the frontend", () => {
  beforeEach(() => {
    installFakeBridge();
    seed("unlocked");
  });

  it("closes tabs, clears the draft, the selection and the search results", async () => {
    useStore.setState({
      tree: {
        folders: [],
        notes: [
          {
            id: "priv1",
            layer_id: PRIVATE_LAYER.id,
            parent_id: null,
            title: "Acquisition Of Northwind",
            folder_path: "",
            aliases: [],
            tags: [],
            properties: {},
            links: [],
            created_at: "",
            updated_at: "",
            size_bytes: 0,
            word_count: 0,
          },
        ],
        locked_layer_ids: [],
      },
      tabs: [{ id: "priv1", title: "Acquisition Of Northwind" }],
      activeNoteId: "priv1",
      dirty: { priv1: true },
      draft: "We will offer 4.2 million",
      selectedIds: ["priv1"],
      searchResults: [
        {
          object_id: "priv1",
          layer_id: PRIVATE_LAYER.id,
          title: "Acquisition Of Northwind",
          path: "x",
          snippet: "4.2 million",
          score: 1,
          tags: [],
          reasons: [],
          signals: { lexical: 1 },
        },
      ],
      openNote: {
        metadata: {
          id: "priv1",
          layer_id: PRIVATE_LAYER.id,
          parent_id: null,
          title: "Acquisition Of Northwind",
          folder_path: "",
          aliases: [],
          tags: [],
          properties: {},
          links: [],
          created_at: "",
          updated_at: "",
          size_bytes: 0,
          word_count: 0,
        },
        content: "We will offer 4.2 million for Northwind.",
      },
    });

    await act(async () => {
      await useStore.getState().lockLayer(PRIVATE_LAYER.id);
    });

    const state = useStore.getState();

    expect(state.tabs).toEqual([]);
    expect(state.openNote).toBeNull();
    expect(state.activeNoteId).toBeNull();
    expect(state.draft).toBeNull();
    expect(state.dirty["priv1"]).toBeUndefined();
    expect(state.selectedIds).toEqual([]);
    expect(state.searchResults).toEqual([]);
    expect(state.plan).toBeNull();

    // The decisive assertion: no decrypted content survives anywhere in the store.
    const serialised = JSON.stringify(state);
    expect(serialised).not.toContain("4.2 million");
    expect(serialised).not.toContain("Northwind");
  });

  it("leaves a public note alone when a private layer locks", async () => {
    useStore.setState({
      tree: {
        folders: [],
        notes: [
          {
            id: "pub1",
            layer_id: PUBLIC_LAYER.id,
            parent_id: null,
            title: "Public Note",
            folder_path: "",
            aliases: [],
            tags: [],
            properties: {},
            links: [],
            created_at: "",
            updated_at: "",
            size_bytes: 0,
            word_count: 0,
          },
        ],
        locked_layer_ids: [],
      },
      tabs: [{ id: "pub1", title: "Public Note" }],
      activeNoteId: "pub1",
      selectedIds: ["pub1"],
    });

    await act(async () => {
      await useStore.getState().lockLayer(PRIVATE_LAYER.id);
    });

    expect(useStore.getState().tabs).toHaveLength(1);
    expect(useStore.getState().selectedIds).toEqual(["pub1"]);
  });
});
