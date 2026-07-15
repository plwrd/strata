/**
 * The live AI request flow: send, stream, cancel, and the remote confirmation.
 *
 * The behaviour under test is the promise the product makes about AI: nothing is
 * sent to a remote model without an explicit confirmation, and what streams back is
 * shown live and can be stopped.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ResponsePanel } from "../features/ai-composer/ResponsePanel";
import { useStore } from "../state/store";
import { emitAIEvent, installFakeBridge, SAMPLE_GRAPH } from "./fakeBridge";
import type { PolicyView, ProviderView } from "../bridge/types";

const LOCAL: ProviderView = {
  provider_id: "ollama",
  display_name: "Ollama",
  is_local: true,
  configured: true,
  requires_api_key: false,
  capabilities: ["text", "streaming"],
  max_context_tokens: 32768,
  note: "Runs on this machine.",
};

const REMOTE: ProviderView = {
  provider_id: "openai",
  display_name: "OpenAI",
  is_local: false,
  configured: true,
  requires_api_key: true,
  capabilities: ["text", "streaming"],
  max_context_tokens: 400000,
  note: "Remote. Your content is sent to OpenAI.",
};

const ALLOWED: PolicyView = {
  verdict: "allowed",
  reason: "Runs on your machine.",
  blocking_layers: [],
  is_remote: false,
  private_object_count: 0,
  object_count: 1,
};

function seed(provider: ProviderView, policy: PolicyView): void {
  useStore.setState({
    connection: "ready",
    graph: SAMPLE_GRAPH,
    selectedIds: ["n1"],
    prompt: "Summarise the selection",
    providers: [provider],
    providerId: provider.provider_id,
    policy,
    aiStreaming: false,
    aiOutput: "",
    aiError: null,
    aiRequestId: null,
  });
}

describe("AI request", () => {
  beforeEach(async () => {
    installFakeBridge();
    // initialise() registers the aiEvent listener that the streaming path needs.
    await useStore.getState().initialise();
    seed(LOCAL, ALLOWED);
  });

  it("sends to a local provider and streams the response", async () => {
    const user = userEvent.setup();
    render(<ResponsePanel />);

    await user.click(screen.getByRole("button", { name: /Ask locally/i }));

    await waitFor(() => {
      expect(useStore.getState().aiRequestId).toBe("req_ai_1");
    });

    // The stream arrives over the aiEvent signal, keyed by request id.
    act(() => {
      emitAIEvent({ requestId: "req_ai_1", kind: "start" });
      emitAIEvent({ requestId: "req_ai_1", kind: "delta", text: "Hello " });
      emitAIEvent({ requestId: "req_ai_1", kind: "delta", text: "world" });
    });

    await waitFor(() => {
      expect(screen.getByLabelText("Model response")).toHaveTextContent(
        "Hello world",
      );
    });

    act(() => {
      emitAIEvent({ requestId: "req_ai_1", kind: "done", output_tokens: 2 });
    });

    await waitFor(() => {
      expect(useStore.getState().aiStreaming).toBe(false);
    });
  });

  it("ignores events for a different request", async () => {
    const user = userEvent.setup();
    render(<ResponsePanel />);
    await user.click(screen.getByRole("button", { name: /Ask locally/i }));
    await waitFor(() =>
      expect(useStore.getState().aiRequestId).toBe("req_ai_1"),
    );

    act(() => {
      emitAIEvent({
        requestId: "some-other-request",
        kind: "delta",
        text: "leaked",
      });
    });

    expect(useStore.getState().aiOutput).not.toContain("leaked");
  });

  it("shows a stop button while streaming and cancels", async () => {
    const user = userEvent.setup();
    render(<ResponsePanel />);
    await user.click(screen.getByRole("button", { name: /Ask locally/i }));
    await waitFor(() =>
      expect(useStore.getState().aiRequestId).toBe("req_ai_1"),
    );

    act(() => {
      emitAIEvent({ requestId: "req_ai_1", kind: "delta", text: "partial" });
    });

    await user.click(await screen.findByRole("button", { name: "Stop" }));

    await waitFor(() => {
      expect(useStore.getState().aiStreaming).toBe(false);
    });
  });

  it("a remote provider requires confirmation before sending", async () => {
    const user = userEvent.setup();
    seed(REMOTE, {
      verdict: "needs_confirmation",
      reason:
        "These layers require confirmation before content is sent to OpenAI.",
      blocking_layers: ["Knowledge"],
      is_remote: true,
      private_object_count: 0,
      object_count: 1,
    });

    render(<ResponsePanel />);
    await user.click(screen.getByRole("button", { name: /Ask \(remote\)/i }));

    // The request does not start yet — the confirmation dialog appears first.
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/Leaving this device/i);
    expect(dialog).toHaveTextContent(/Send to OpenAI/i);
    expect(useStore.getState().aiRequestId).toBeNull();

    await user.click(screen.getByRole("button", { name: "Send to OpenAI" }));

    await waitFor(() => {
      expect(useStore.getState().aiRequestId).toBe("req_ai_1");
    });
  });

  it("cancelling the remote confirmation sends nothing", async () => {
    const user = userEvent.setup();
    seed(REMOTE, {
      verdict: "needs_confirmation",
      reason: "Confirm first.",
      blocking_layers: ["Knowledge"],
      is_remote: true,
      private_object_count: 0,
      object_count: 1,
    });

    render(<ResponsePanel />);
    await user.click(screen.getByRole("button", { name: /Ask \(remote\)/i }));
    await screen.findByRole("alertdialog");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(useStore.getState().aiRequestId).toBeNull();
  });

  it("a denied policy disables the send button entirely", () => {
    seed(REMOTE, {
      verdict: "denied",
      reason: "“Deals” may only be sent to a model on this machine.",
      blocking_layers: ["Deals"],
      is_remote: true,
      private_object_count: 1,
      object_count: 1,
    });

    render(<ResponsePanel />);

    expect(
      screen.getByRole("button", { name: /Ask \(remote\)/i }),
    ).toBeDisabled();
  });

  it("surfaces a stream error", async () => {
    const user = userEvent.setup();
    render(<ResponsePanel />);
    await user.click(screen.getByRole("button", { name: /Ask locally/i }));
    await waitFor(() =>
      expect(useStore.getState().aiRequestId).toBe("req_ai_1"),
    );

    act(() => {
      emitAIEvent({
        requestId: "req_ai_1",
        kind: "error",
        error: "The model refused.",
      });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The model refused.",
    );
    expect(useStore.getState().aiStreaming).toBe(false);
  });
});
