/**
 * The graph control bar — the surface for M5 advanced selection and display.
 *
 * The advanced-selection buttons only appear once there is an anchor, and the
 * shortest-path button only once two nodes are selected: a control that acts on
 * "the selection" should not be reachable before a selection exists.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { GraphControls } from "../features/graph/GraphControls";
import { useStore } from "../state/store";
import { installFakeBridge, SAMPLE_GRAPH } from "./fakeBridge";

function reset(): void {
  useStore.setState({
    selectedIds: [],
    lastAnchorId: null,
    graph: SAMPLE_GRAPH,
    dimension: "2d",
    semanticEdges: false,
    clusterColors: false,
  });
}

describe("GraphControls", () => {
  beforeEach(() => {
    installFakeBridge();
    reset();
  });

  it("hides the selection tools until there is an anchor", () => {
    render(<GraphControls />);
    expect(screen.queryByRole("button", { name: "Connected" })).toBeNull();
  });

  it("shows the lasso hint only in 2D", () => {
    const { rerender } = render(<GraphControls />);
    expect(screen.getByText(/shift-drag to lasso/)).toBeInTheDocument();

    useStore.setState({ dimension: "3d" });
    rerender(<GraphControls />);
    expect(screen.queryByText(/shift-drag to lasso/)).toBeNull();
  });

  it("expands to the connected component from the anchor", async () => {
    useStore.getState().select("n4");
    render(<GraphControls />);

    await userEvent.click(screen.getByRole("button", { name: "Connected" }));

    expect(useStore.getState().selectedIds).toEqual(
      expect.arrayContaining(["n1", "n2", "n3", "n4"]),
    );
  });

  it("offers the path button only once two nodes are selected", () => {
    useStore.getState().select("n4");
    const { rerender } = render(<GraphControls />);
    expect(screen.queryByRole("button", { name: "Path" })).toBeNull();

    useStore.getState().toggleSelect("n3");
    rerender(<GraphControls />);
    expect(screen.getByRole("button", { name: "Path" })).toBeInTheDocument();
  });

  it("toggles semantic edges through the store", async () => {
    render(<GraphControls />);
    await userEvent.click(screen.getByLabelText("Semantic edges"));
    expect(useStore.getState().semanticEdges).toBe(true);
  });
});
