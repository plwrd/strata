/**
 * The transactional AI change engine, in the UI.
 *
 * The behaviour under test is the review gate: a proposed plan is shown as a diff,
 * destructive operations are NOT pre-approved, and only ticked operations apply.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { OperationsPanel } from "../features/operations/OperationsPanel";
import { useStore } from "../state/store";
import {
  emitPlanEvent,
  installFakeBridge,
  planListeners,
  SAMPLE_GRAPH,
} from "./fakeBridge";

const planListenerCount = (): number => planListeners.length;

function seed(): void {
  useStore.setState({
    connection: "ready",
    graph: SAMPLE_GRAPH,
    selectedIds: ["n1"],
    layers: [
      {
        id: "layer_a",
        display_name: "Knowledge",
        visibility: "public",
        state: "mounted",
        sharing_mode: "personal",
        storage: "markdown",
        storage_version: 1,
        created_at: "",
        updated_at: "",
        color: "layer-public",
        ai_policy: {} as never,
      },
    ],
    providerId: "ollama",
    model: "llama3",
  });
}

async function generateAndReview(): Promise<void> {
  const user = userEvent.setup();
  render(<OperationsPanel />);
  // The planEvent subscription is set up asynchronously on mount; let it register.
  await waitFor(() => expect(planListenerCount()).toBeGreaterThan(0));

  await user.type(
    screen.getByLabelText("Operation prompt"),
    "Organise my notes",
  );
  await user.click(screen.getByRole("button", { name: /Propose changes/i }));

  // The plan streams back over planEvent; the panel then reviews it.
  await act(async () => {
    emitPlanEvent({
      requestId: "req_plan_1",
      kind: "plan",
      plan: { id: "plan_test", summary: "A plan", operations: [] },
    });
    await Promise.resolve();
  });
}

describe("OperationsPanel", () => {
  beforeEach(() => {
    installFakeBridge();
    seed();
  });

  it("proposes a plan and shows it as a diff", async () => {
    await generateAndReview();

    await waitFor(() => {
      expect(screen.getByText(/# Proposed Note/)).toBeInTheDocument();
    });
    expect(screen.getByText(/capture the idea/i)).toBeInTheDocument();
    // Both operations are listed.
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  });

  it("does not pre-approve a destructive operation", async () => {
    await generateAndReview();
    await screen.findByText(/# Proposed Note/);

    const checkboxes = screen.getAllByRole("checkbox");
    // The additive create is pre-ticked; the destructive delete is not.
    expect(checkboxes[0]).toBeChecked();
    expect(checkboxes[1]).not.toBeChecked();
  });

  it("marks a destructive operation visibly", async () => {
    await generateAndReview();
    await screen.findByText(/# Proposed Note/);

    expect(screen.getByText("changes content")).toBeInTheDocument();
  });

  it("applies the approved operations and offers undo", async () => {
    const user = userEvent.setup();
    await generateAndReview();
    await screen.findByText(/# Proposed Note/);

    await user.click(screen.getByRole("button", { name: /Apply 1 change/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/A snapshot was taken first/i),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("rejecting the plan clears it", async () => {
    const user = userEvent.setup();
    await generateAndReview();
    await screen.findByText(/# Proposed Note/);

    await user.click(screen.getByRole("button", { name: /Reject all/i }));

    expect(screen.queryByText(/# Proposed Note/)).not.toBeInTheDocument();
  });

  it("notes mode sends the mode, the count, and the selection", async () => {
    const requests: Record<string, unknown>[] = [];
    installFakeBridge({
      onRequest: (object, method, raw) => {
        if (object === "operations" && method === "generate_plan") {
          const parsed = JSON.parse(raw) as {
            payload: Record<string, unknown>;
          };
          requests.push(parsed.payload);
        }
      },
    });
    seed();

    const user = userEvent.setup();
    render(<OperationsPanel />);
    await waitFor(() => expect(planListenerCount()).toBeGreaterThan(0));

    await user.selectOptions(screen.getByLabelText("Generation mode"), "notes");
    await user.selectOptions(screen.getByLabelText("Number of notes"), "3");
    await user.type(
      screen.getByLabelText("Operation prompt"),
      "Split this note into topics",
    );
    await user.click(screen.getByRole("button", { name: "Generate notes" }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({
      mode: "notes",
      note_count: 3,
      object_ids: ["n1"],
    });
  });

  it("explains what a selection contributes in notes mode", async () => {
    const user = userEvent.setup();
    render(<OperationsPanel />);

    await user.selectOptions(screen.getByLabelText("Generation mode"), "notes");

    expect(
      screen.getByText(/content is shared with the model as context/i),
    ).toBeInTheDocument();
  });

  it("shows an error when the model returns no plan", async () => {
    const user = userEvent.setup();
    render(<OperationsPanel />);
    await waitFor(() => expect(planListenerCount()).toBeGreaterThan(0));
    await user.type(screen.getByLabelText("Operation prompt"), "gibberish");
    await user.click(screen.getByRole("button", { name: /Propose changes/i }));

    await act(async () => {
      emitPlanEvent({
        requestId: "req_plan_1",
        kind: "error",
        error: "The model did not return a plan.",
      });
      await Promise.resolve();
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /did not return a plan/i,
    );
  });
});
