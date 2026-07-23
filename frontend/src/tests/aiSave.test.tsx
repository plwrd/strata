/**
 * Saving an AI answer as a permanent asset, and the ask-workspace flow.
 *
 * What matters: the save buttons appear only for a finished answer, saving
 * sends the visible content with its execution id (provenance), the sources
 * the backend chose are displayed rather than implied, and a new thread
 * actually resets the conversation.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ResponsePanel } from "../features/ai-composer/ResponsePanel";
import { useStore } from "../state/store";
import { captured, installFakeBridge } from "./fakeBridge";

function primeFinishedAnswer(): void {
  useStore.setState({
    prompt: "What did I decide about encryption?",
    providerId: "ollama",
    providers: [
      {
        provider_id: "ollama",
        display_name: "Ollama",
        is_local: true,
        configured: true,
        requires_api_key: false,
        capabilities: ["text", "streaming"],
        max_context_tokens: 32768,
        note: "",
      },
    ],
    policy: {
      verdict: "allowed",
      reason: "",
      blocking_layers: [],
      is_remote: false,
      private_object_count: 0,
      object_count: 1,
    },
    aiStreaming: false,
    aiOutput: "You chose XChaCha20-Poly1305.",
    aiExecutionId: "exec_fake_1",
    conversationId: "conv_fake_1",
    aiSources: [
      { object_id: "n1", title: "Encryption Architecture", is_private: false },
    ],
    openNote: null,
  });
}

describe("ResponsePanel — save and thread", () => {
  beforeEach(() => {
    installFakeBridge();
    primeFinishedAnswer();
  });

  it("shows which notes the model saw", () => {
    render(<ResponsePanel />);

    expect(screen.getByText("Encryption Architecture")).toBeInTheDocument();
  });

  it("saves the visible answer as a report with its execution id", async () => {
    render(<ResponsePanel />);

    await userEvent.click(
      screen.getByRole("button", { name: "Save as report" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Saved as"),
    );
    const payload = captured.find((entry) => entry["target"] === "report");
    expect(payload?.["content"]).toBe("You chose XChaCha20-Poly1305.");
    expect(payload?.["execution_id"]).toBe("exec_fake_1");
    expect(payload?.["title"]).toBe("What did I decide about encryption?");
  });

  it("starting a new thread resets the conversation", async () => {
    render(<ResponsePanel />);

    await userEvent.click(screen.getByRole("button", { name: "New thread" }));

    expect(useStore.getState().conversationId).toBeNull();
    expect(useStore.getState().aiOutput).toBe("");
  });

  it("asking with nothing selected retrieves context server-side", async () => {
    useStore.setState({ selectedIds: [], askWorkspace: true });

    await act(async () => {
      await useStore.getState().sendToModel(false);
    });

    const payload = captured.find((entry) => "retrieve" in entry);
    expect(payload?.["retrieve"]).toBe(true);
    expect(useStore.getState().aiSources).toHaveLength(1);
    expect(useStore.getState().conversationId).toBe("conv_fake_1");
  });
});
