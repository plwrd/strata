/**
 * The AI history panel — the UI over the workspace's persisted AI memory.
 *
 * What matters here: the list is fetched from Python (not invented client-side),
 * redacted records show their badge and never their prompt, clearing is a
 * two-step action, and every load state (loading, error, empty, ready) renders
 * something honest.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AIHistoryPanel } from "../features/ai-composer/AIHistoryPanel";
import { installFakeBridge, sampleExecution } from "./fakeBridge";

async function openHistory(): Promise<void> {
  await userEvent.click(screen.getByRole("button", { name: "AI history" }));
}

describe("AIHistoryPanel", () => {
  it("lists persisted executions with provider, locality and prompt", async () => {
    installFakeBridge({ executions: [sampleExecution()] });
    render(<AIHistoryPanel />);

    await openHistory();

    expect(
      await screen.findByText("What did I decide about encryption?"),
    ).toBeInTheDocument();
    expect(screen.getByText("ollama · llama3")).toBeInTheDocument();
    expect(screen.getByText("local")).toBeInTheDocument();
    expect(screen.queryByText("redacted")).not.toBeInTheDocument();
  });

  it("shows redacted records as metadata only — never the prompt", async () => {
    installFakeBridge({
      executions: [
        sampleExecution({
          id: "exec_private",
          prompt: "",
          response_text: "",
          redacted: true,
          private_source_count: 2,
        }),
      ],
    });
    render(<AIHistoryPanel />);

    await openHistory();

    expect(await screen.findByText("redacted")).toBeInTheDocument();
    expect(
      screen.queryByText("What did I decide about encryption?"),
    ).not.toBeInTheDocument();
  });

  it("shows an honest empty state when nothing was recorded", async () => {
    installFakeBridge({ executions: [] });
    render(<AIHistoryPanel />);

    await openHistory();

    expect(
      await screen.findByText(/No AI activity recorded yet/),
    ).toBeInTheDocument();
  });

  it("clears history only after the second, explicit confirmation", async () => {
    installFakeBridge({ executions: [sampleExecution()] });
    render(<AIHistoryPanel />);
    await openHistory();
    await screen.findByText("What did I decide about encryption?");

    await userEvent.click(
      screen.getByRole("button", { name: "Clear history…" }),
    );
    // Step one arms the action; nothing is deleted yet.
    expect(
      screen.getByText("What did I decide about encryption?"),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Delete history" }),
    );

    await waitFor(() =>
      expect(
        screen.queryByText("What did I decide about encryption?"),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/No AI activity recorded yet/)).toBeInTheDocument();
  });

  it("keeps the history when the user backs out of clearing", async () => {
    installFakeBridge({ executions: [sampleExecution()] });
    render(<AIHistoryPanel />);
    await openHistory();
    await screen.findByText("What did I decide about encryption?");

    await userEvent.click(
      screen.getByRole("button", { name: "Clear history…" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Keep it" }));

    expect(
      screen.getByText("What did I decide about encryption?"),
    ).toBeInTheDocument();
  });

  it("surfaces a load failure with a retry", async () => {
    installFakeBridge({
      failWith: { code: "internal", message: "History unavailable." },
    });
    render(<AIHistoryPanel />);

    await openHistory();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "History unavailable.",
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
