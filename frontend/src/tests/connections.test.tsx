/**
 * Connection suggestions — offers with reasons, accepted through the plan flow.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ConnectionSuggestions } from "../features/links/ConnectionSuggestions";
import type { ConnectionSuggestion } from "../bridge/types";
import { useStore } from "../state/store";
import { installFakeBridge } from "./fakeBridge";

const SUGGESTION: ConnectionSuggestion = {
  note_a: "n1",
  note_a_title: "Encryption Architecture",
  note_b: "n2",
  note_b_title: "Threat Model",
  layer_id: "layer_a",
  kind: "similar",
  score: 0.72,
  explanation:
    "Content is 72% similar — they discuss related material without being linked.",
  excerpt: "…the threat model says…",
  suggested_relationship: "relates_to",
};

async function openNote(): Promise<void> {
  await act(async () => {
    await useStore.getState().openNoteById("n1");
  });
}

describe("ConnectionSuggestions", () => {
  beforeEach(() => {
    useStore.setState({ openNote: null });
  });

  it("discovers on demand and shows the reason", async () => {
    installFakeBridge({ suggestions: [SUGGESTION] });
    await openNote();
    render(<ConnectionSuggestions />);

    await userEvent.click(screen.getByRole("button", { name: "Discover" }));

    expect(await screen.findByText(/72% similar/)).toBeInTheDocument();
    expect(screen.getByText("similar")).toBeInTheDocument();
  });

  it("accepting applies the relationship through the plan flow", async () => {
    let applied = false;
    installFakeBridge({
      suggestions: [SUGGESTION],
      onRequest: (_object, method) => {
        if (method === "apply_plan") applied = true;
      },
    });
    await openNote();
    render(<ConnectionSuggestions />);
    await userEvent.click(screen.getByRole("button", { name: "Discover" }));
    await screen.findByText(/72% similar/);

    await userEvent.click(
      screen.getByRole("button", { name: "Add “relates to”" }),
    );

    await waitFor(() => expect(applied).toBe(true));
    expect(await screen.findByRole("status")).toHaveTextContent("Added:");
  });

  it("dismiss removes the offer without applying anything", async () => {
    let applied = false;
    installFakeBridge({
      suggestions: [SUGGESTION],
      onRequest: (_object, method) => {
        if (method === "apply_plan") applied = true;
      },
    });
    await openNote();
    render(<ConnectionSuggestions />);
    await userEvent.click(screen.getByRole("button", { name: "Discover" }));
    await screen.findByText(/72% similar/);

    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(screen.queryByText(/72% similar/)).not.toBeInTheDocument();
    expect(applied).toBe(false);
  });

  it("says so when there is nothing to suggest", async () => {
    installFakeBridge({ suggestions: [] });
    await openNote();
    render(<ConnectionSuggestions />);

    await userEvent.click(screen.getByRole("button", { name: "Discover" }));

    expect(
      await screen.findByText(/neighbourhood looks connected/),
    ).toBeInTheDocument();
  });
});
