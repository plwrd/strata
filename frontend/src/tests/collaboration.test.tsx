/**
 * The collaboration panel — sharing, and the conflict surface.
 *
 * The panel is a thin control over the Python service, but two behaviours are
 * load-bearing and tested here: a personal layer can be shared (and then shows
 * its invite handle and peers), and a surfaced conflict is resolvable. A locked
 * layer must never appear, because the service reveals nothing about it.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { CollaborationPanel } from "../features/collaboration/CollaborationPanel";
import { useStore } from "../state/store";
import { installFakeBridge } from "./fakeBridge";

function layer(id: string, state: string, name: string) {
  return {
    id,
    display_name: name,
    visibility: id.includes("p") ? "private" : "public",
    state,
    sharing_mode: "personal",
    storage: "markdown",
    storage_version: 1,
    created_at: "",
    updated_at: "",
    color: "layer-public",
    ai_policy: {} as never,
  };
}

function reset(): void {
  useStore.setState({
    layers: [
      layer("layer_a", "mounted", "Knowledge"),
      layer("layer_locked", "locked", "Secret"),
    ] as never,
    collab: {},
    collabConflicts: {},
  });
}

describe("CollaborationPanel", () => {
  beforeEach(() => {
    installFakeBridge();
    reset();
  });

  it("lists readable layers and hides locked ones", async () => {
    render(<CollaborationPanel />);
    await waitFor(() =>
      expect(screen.getByText("Knowledge")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Secret")).toBeNull();
  });

  it("shares a personal layer and reveals its invite handle", async () => {
    render(<CollaborationPanel />);
    await waitFor(() =>
      expect(screen.getByText("Knowledge")).toBeInTheDocument(),
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Share this layer" }),
    );

    await waitFor(() =>
      expect(screen.getByText("doc-abc123")).toBeInTheDocument(),
    );
    expect(screen.getByText("shared")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Sync now" }),
    ).toBeInTheDocument();
  });

  it("surfaces conflicts and resolves them", async () => {
    useStore.setState({
      layers: [layer("layer_conflict", "mounted", "Team notes")] as never,
      collab: {},
      collabConflicts: {},
    });

    render(<CollaborationPanel />);
    await waitFor(() =>
      expect(screen.getByText(/nothing was lost/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/Interview/)).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Keep in Conflicts/" }),
    );
    await waitFor(() =>
      expect(screen.queryByText(/nothing was lost/)).toBeNull(),
    );
  });
});
