/**
 * The AI Context Composer, rendered.
 *
 * The behaviour under test is the promise the product makes: the panel shows
 * exactly what would be sent, and private content cannot leave without a
 * confirmed privacy review.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AIComposerPanel } from "../features/ai-composer/AIComposerPanel";
import { useStore } from "../state/store";
import { installFakeBridge, planFor, SAMPLE_GRAPH } from "./fakeBridge";
import { stubClipboard } from "./setup";

function seed(selectedIds: string[] = []): void {
  useStore.setState({
    connection: "ready",
    graph: SAMPLE_GRAPH,
    selectedIds,
    plan: null,
    planError: null,
    prompt: "",
    providers: [],
    layers: [],
  });
}

describe("AI Context Composer", () => {
  beforeEach(() => {
    installFakeBridge();
    seed();
  });

  it("says nothing is selected before anything is selected", () => {
    render(<AIComposerPanel />);

    expect(screen.getByText(/Select nodes in the graph/i)).toBeInTheDocument();
  });

  it("lists every source that would be included, with its stable id", async () => {
    seed(["n1", "n2"]);
    await useStore.getState().refreshPlan();

    render(<AIComposerPanel />);

    await waitFor(() => {
      expect(screen.getByText("STRATA-SOURCE-001")).toBeInTheDocument();
    });
    expect(screen.getByText("STRATA-SOURCE-002")).toBeInTheDocument();
    expect(screen.getByText("Encryption Architecture")).toBeInTheDocument();
    expect(screen.getByText("Threat Model")).toBeInTheDocument();
  });

  it("shows the token estimate that Python computed", async () => {
    seed(["n1", "n2"]);
    await useStore.getState().refreshPlan();

    render(<AIComposerPanel />);

    await waitFor(() => {
      expect(screen.getByText(/~80 tokens/)).toBeInTheDocument();
    });
  });

  it("removing a source from the tray deselects it", async () => {
    const user = userEvent.setup();
    seed(["n1", "n2"]);
    await useStore.getState().refreshPlan();

    render(<AIComposerPanel />);
    await screen.findByText("STRATA-SOURCE-001");

    await user.click(
      screen.getByRole("button", {
        name: /Remove Encryption Architecture from the context/i,
      }),
    );

    await waitFor(() => {
      expect(useStore.getState().selectedIds).toEqual(["n2"]);
    });
  });

  it("a slash command fills the prompt without sending anything", async () => {
    const user = userEvent.setup();
    seed(["n1"]);
    await useStore.getState().refreshPlan();

    render(<AIComposerPanel />);
    await user.click(screen.getByRole("button", { name: "/ commands" }));
    await user.click(screen.getByText("/find-gaps"));

    await waitFor(() => {
      expect(useStore.getState().prompt).toMatch(/Identify what is missing/);
    });
  });

  it("reports that no provider is configured, and does not offer a send button", async () => {
    seed(["n1"]);
    useStore.setState({
      providers: [
        {
          provider_id: "openai",
          display_name: "OpenAI",
          is_local: false,
          configured: false,
          streaming: true,
          structured_output: true,
          embeddings: true,
          vision: true,
          max_context_tokens: 400000,
          note: "Remote. Arrives in Milestone 7.",
        },
      ],
    });
    await useStore.getState().refreshPlan();

    render(<AIComposerPanel />);

    expect(
      screen.getByText(/No AI provider is configured/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /OpenAI/ })).toBeDisabled();
  });

  it("exports to the clipboard through the bridge", async () => {
    const user = userEvent.setup();
    // After userEvent.setup(): it installs its own clipboard stub, which would
    // otherwise replace this one.
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);

    seed(["n1"]);
    useStore.setState({ prompt: "Explain the design" });
    await useStore.getState().refreshPlan();

    render(<AIComposerPanel />);
    await user.click(
      screen.getByRole("button", { name: /Copy to clipboard/i }),
    );

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledOnce();
    });
    expect(writeText.mock.calls[0]![0]).toContain("Explain the design");
    // The clipboard is a leak path and the UI must say so.
    expect(
      await screen.findByText(/clipboard managers may retain/i),
    ).toBeInTheDocument();
  });

  it("opens the privacy review instead of exporting when private content is included", async () => {
    const user = userEvent.setup();
    seed(["n1"]);
    const privatePlan = planFor(["n1"]);
    privatePlan.sources[0]!.is_private = true;
    privatePlan.private_source_count = 1;
    privatePlan.private_layer_names = ["Research"];
    useStore.setState({ plan: privatePlan });

    render(<AIComposerPanel />);
    await user.click(screen.getByRole("button", { name: /Export Markdown/i }));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/Review before exporting/i);
    expect(dialog).toHaveTextContent(/will not be encrypted/i);
    expect(dialog).toHaveTextContent("Research");
    // Every private source is named: "1 private note" is not informed consent.
    expect(dialog).toHaveTextContent("STRATA-SOURCE-001");
  });

  it("cancelling the privacy review exports nothing", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);

    seed(["n1"]);
    const privatePlan = planFor(["n1"]);
    privatePlan.sources[0]!.is_private = true;
    privatePlan.private_source_count = 1;
    useStore.setState({ plan: privatePlan });

    render(<AIComposerPanel />);
    await user.click(
      screen.getByRole("button", { name: /Copy to clipboard/i }),
    );
    await screen.findByRole("alertdialog");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(writeText).not.toHaveBeenCalled();
  });
});
