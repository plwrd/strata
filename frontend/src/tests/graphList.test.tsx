/**
 * The accessible graph.
 *
 * The 3D canvas is `aria-hidden`, so this tree *is* the graph for a screen-reader
 * user. If it stops working, the graph stops existing for them.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { GraphList } from "../features/graph/GraphList";
import { SAMPLE_GRAPH } from "./fakeBridge";

function setup(selectedIds: string[] = []) {
  const onSelect = vi.fn();
  const onOpen = vi.fn();
  const onSelectAll = vi.fn();
  render(
    <GraphList
      graph={SAMPLE_GRAPH}
      selectedIds={selectedIds}
      onSelect={onSelect}
      onOpen={onOpen}
      onSelectAll={onSelectAll}
    />,
  );
  return { onSelect, onOpen, onSelectAll };
}

describe("GraphList", () => {
  it("exposes the graph as a multi-selectable tree", () => {
    setup();

    const tree = screen.getByRole("tree", { name: "Knowledge graph" });
    expect(tree).toHaveAttribute("aria-multiselectable", "true");
    expect(screen.getAllByRole("treeitem")).toHaveLength(
      SAMPLE_GRAPH.nodes.length,
    );
  });

  it("announces the selection state", () => {
    setup(["n1"]);

    expect(screen.getByRole("status")).toHaveTextContent("1 selected");
    expect(screen.getByRole("status")).toHaveTextContent(
      "1 locked layer(s) are hidden",
    );
  });

  it("marks selected items with aria-selected", () => {
    setup(["n1"]);

    const item = screen.getByRole("treeitem", {
      name: /Encryption Architecture/,
    });
    expect(item).toHaveAttribute("aria-selected", "true");
  });

  it("never announces a locked node title", () => {
    setup();

    const locked = screen.getByRole("treeitem", {
      name: /Locked knowledge object/,
    });
    expect(locked).toBeInTheDocument();
    expect(locked.textContent).not.toMatch(/Research|secret/i);
  });

  it("space toggles selection, enter opens", async () => {
    const user = userEvent.setup();
    const { onSelect, onOpen } = setup();

    const item = screen.getByRole("treeitem", { name: /Threat Model/ });
    item.focus();

    await user.keyboard(" ");
    expect(onSelect).toHaveBeenCalledWith("n2", { ctrl: true, shift: false });

    await user.keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledWith("n2");
  });

  it("does not open a locked node", async () => {
    const user = userEvent.setup();
    const { onOpen } = setup();

    screen.getByRole("treeitem", { name: /Locked knowledge object/ }).focus();
    await user.keyboard("{Enter}");

    expect(onOpen).not.toHaveBeenCalled();
  });

  it("arrow keys move focus between items", async () => {
    const user = userEvent.setup();
    setup();

    const items = screen.getAllByRole("treeitem");
    items[0]!.focus();
    await user.keyboard("{ArrowDown}");

    expect(document.activeElement).toBe(items[1]);
  });

  it("ctrl+a selects every node", async () => {
    const user = userEvent.setup();
    const { onSelectAll } = setup();

    screen.getAllByRole("treeitem")[0]!.focus();
    await user.keyboard("{Control>}a{/Control}");

    expect(onSelectAll).toHaveBeenCalledWith(
      SAMPLE_GRAPH.nodes.map((node) => node.id),
    );
  });
});
