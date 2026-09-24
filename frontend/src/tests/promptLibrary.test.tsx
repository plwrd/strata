/**
 * The prompt library panel: pick to fill, save the current prompt, count uses.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { PromptLibraryPanel } from "../features/ai-composer/PromptLibraryPanel";
import type { SavedPrompt } from "../bridge/types";
import { useStore } from "../state/store";
import { installFakeBridge } from "./fakeBridge";

const WEEKLY: SavedPrompt = {
  id: "prompt_weekly",
  name: "Weekly digest",
  description: "",
  category: "weekly-review",
  prompt_text: "Summarise what changed this week, with citations.",
  model_preference: "",
  temperature: null,
  version: 3,
  usage_count: 7,
  created_at: "2026-07-01T10:00:00+00:00",
  updated_at: "2026-07-14T10:00:00+00:00",
  last_used_at: "2026-07-20T10:00:00+00:00",
};

describe("PromptLibraryPanel", () => {
  beforeEach(() => {
    useStore.setState({ prompt: "" });
  });

  it("filling from a saved prompt sets the editor and counts the use", async () => {
    installFakeBridge({ prompts: [WEEKLY] });
    render(<PromptLibraryPanel />);

    await screen.findByText(/Weekly digest · v3 · used 7×/);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Saved prompts" }),
      "prompt_weekly",
    );

    await waitFor(() =>
      expect(useStore.getState().prompt).toBe(
        "Summarise what changed this week, with citations.",
      ),
    );
  });

  it("saves the current prompt under a name", async () => {
    installFakeBridge();
    useStore.setState({ prompt: "Extract every decision from the sources." });
    render(<PromptLibraryPanel />);

    await userEvent.click(screen.getByRole("button", { name: "Save prompt…" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Prompt name" }),
      "Decision extractor",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/Saved prompts \(1\)/);
  });

  it("says so when the library is empty", async () => {
    installFakeBridge({ prompts: [] });
    render(<PromptLibraryPanel />);

    expect(await screen.findByText("No saved prompts")).toBeInTheDocument();
  });
});
