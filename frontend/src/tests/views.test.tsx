/**
 * Structured views: the type switcher and the renderers.
 *
 * The data comes from Python (the query); these tests assert the UI renders it
 * faithfully and switches type without losing the query.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ViewsStage } from "../features/views/ViewsStage";
import { useStore } from "../state/store";
import { installFakeBridge } from "./fakeBridge";

describe("ViewsStage", () => {
  beforeEach(() => {
    installFakeBridge();
    useStore.setState({ connection: "ready" });
  });

  it("renders a table of notes by default", async () => {
    render(<ViewsStage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Alpha" })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Beta" })).toBeInTheDocument();
    // Property columns are present.
    expect(
      screen.getByRole("columnheader", { name: "status" }),
    ).toBeInTheDocument();
  });

  it("switches to cards without losing the data", async () => {
    const user = userEvent.setup();
    render(<ViewsStage />);
    await screen.findByRole("button", { name: "Alpha" });

    await user.click(screen.getByRole("tab", { name: /Cards/ }));

    await waitFor(() => {
      expect(document.querySelector(".cards")).toBeInTheDocument();
    });
    expect(screen.getByText("Alpha")).toBeInTheDocument();
  });

  it("renders kanban columns when grouped", async () => {
    const user = userEvent.setup();
    render(<ViewsStage />);
    await screen.findByRole("button", { name: "Alpha" });

    await user.click(screen.getByRole("tab", { name: /Kanban/ }));
    // Group by status via the toolbar.
    await waitFor(() =>
      expect(screen.getByLabelText("Group")).toBeInTheDocument(),
    );
    await user.selectOptions(screen.getByLabelText("Group"), "status");

    await waitFor(() => {
      expect(screen.getByText("in progress")).toBeInTheDocument();
    });
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  it("adds a filter row", async () => {
    const user = userEvent.setup();
    render(<ViewsStage />);
    await screen.findByRole("button", { name: "Alpha" });

    await user.click(screen.getByRole("button", { name: "+ Filter" }));

    expect(screen.getByLabelText("Filter field")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter operator")).toBeInTheDocument();
  });

  it("opens a note from a row", async () => {
    const user = userEvent.setup();
    render(<ViewsStage />);
    const alpha = await screen.findByRole("button", { name: "Alpha" });

    await user.click(alpha);

    await waitFor(() => {
      expect(useStore.getState().mode).toBe("focus");
    });
  });

  it("switches to the timeline view", async () => {
    const user = userEvent.setup();
    render(<ViewsStage />);
    await screen.findByRole("button", { name: "Alpha" });

    await user.click(screen.getByRole("tab", { name: /Timeline/ }));

    await waitFor(() => {
      expect(document.querySelector(".timeline")).toBeInTheDocument();
    });
  });
});
