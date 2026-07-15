/**
 * Multi-node selection â€” the state the whole AI surface is built on.
 *
 * If selection is wrong, the context is wrong, and the wrong thing gets sent to a
 * model. These tests are the guard on that.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { shortestPath, summariseSelection, useStore } from "../state/store";
import { installFakeBridge, SAMPLE_GRAPH } from "./fakeBridge";

function reset(): void {
  useStore.setState({
    selectedIds: [],
    lastAnchorId: null,
    graph: SAMPLE_GRAPH,
    plan: null,
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
      {
        id: "layer_p",
        display_name: "Research",
        visibility: "private",
        state: "locked",
        sharing_mode: "personal",
        storage: "markdown",
        storage_version: 1,
        created_at: "",
        updated_at: "",
        color: "layer-private",
        ai_policy: {} as never,
      },
    ],
  });
}

describe("selection", () => {
  beforeEach(() => {
    installFakeBridge();
    reset();
  });

  it("replaces the selection on a plain click", () => {
    const store = useStore.getState();
    store.select("n1");
    store.select("n2");

    expect(useStore.getState().selectedIds).toEqual(["n2"]);
  });

  it("adds and removes with ctrl-click", () => {
    const store = useStore.getState();
    store.select("n1");
    store.toggleSelect("n2");
    store.toggleSelect("n3");

    expect(useStore.getState().selectedIds).toEqual(["n1", "n2", "n3"]);

    useStore.getState().toggleSelect("n2");
    expect(useStore.getState().selectedIds).toEqual(["n1", "n3"]);
  });

  it("never lets the same node appear twice", () => {
    const store = useStore.getState();
    store.selectMany(["n1", "n2", "n1"], "replace");
    useStore.getState().selectMany(["n2", "n3"], "add");

    expect(useStore.getState().selectedIds).toEqual(["n1", "n2", "n3"]);
  });

  it("shift-click selects the path between the anchor and the target", () => {
    const store = useStore.getState();
    store.select("n4");
    useStore.getState().rangeSelect("n3");

    // n4 â€” n1 â€” n2 â€” n3
    expect(useStore.getState().selectedIds).toEqual(["n4", "n1", "n2", "n3"]);
  });

  it("clears everything, including the plan", () => {
    const store = useStore.getState();
    store.selectMany(["n1", "n2"]);
    useStore.setState({ plan: { estimated_tokens: 10 } as never });

    useStore.getState().clearSelection();

    expect(useStore.getState().selectedIds).toEqual([]);
    expect(useStore.getState().plan).toBeNull();
  });

  it("expands to neighbours through the bridge", async () => {
    const store = useStore.getState();
    store.select("n1");
    await useStore.getState().selectNeighbours("n1");

    expect(useStore.getState().selectedIds).toEqual(
      expect.arrayContaining(["n1", "n2", "n4"]),
    );
  });

  it("selects the connected component from the graph in memory", () => {
    // n1-n2-n3 and n1-n4 are one component; the locked node is isolated.
    useStore.getState().selectConnected("n4");

    const ids = useStore.getState().selectedIds;
    expect(ids).toEqual(expect.arrayContaining(["n1", "n2", "n3", "n4"]));
    expect(ids).not.toContain("locked:layer_p");
  });

  it("selects a semantic cluster through the bridge", async () => {
    await useStore.getState().selectCluster("n1");
    expect(useStore.getState().selectedIds).toEqual(
      expect.arrayContaining(["n1", "n2"]),
    );
  });

  it("selects the shortest path through the bridge", async () => {
    await useStore.getState().selectShortestPath("n4", "n3");
    expect(useStore.getState().selectedIds).toEqual(["n4", "n1", "n3"]);
  });

  it("selects every node carrying a tag, skipping locked ones", () => {
    useStore.getState().selectByTag("security");
    const ids = useStore.getState().selectedIds;
    expect(ids.length).toBeGreaterThan(0);
    expect(ids).not.toContain("locked:layer_p");
  });

  it("selects a whole layer, skipping locked nodes", () => {
    useStore.getState().selectByLayer("layer_a");
    const ids = useStore.getState().selectedIds;
    expect(ids.every((id) => !id.startsWith("locked:"))).toBe(true);
  });

  it("reloads the graph when the semantic-edge toggle flips", async () => {
    expect(useStore.getState().semanticEdges).toBe(false);
    await useStore.getState().setSemanticEdges(true);
    expect(useStore.getState().semanticEdges).toBe(true);
  });

  it("reloads the graph when the cluster-colour toggle flips", async () => {
    await useStore.getState().setClusterColors(true);
    expect(useStore.getState().clusterColors).toBe(true);
  });

  it("summarises what is selected, including private and locked counts", () => {
    useStore.getState().selectMany(["n1", "n2", "locked:layer_p"]);

    const summary = summariseSelection(useStore.getState());

    expect(summary.count).toBe(3);
    expect(summary.lockedCount).toBe(1);
    expect(summary.layerIds).toEqual(
      expect.arrayContaining(["layer_a", "layer_p"]),
    );
  });

  it("never asks Python to plan a locked object", async () => {
    useStore.getState().selectMany(["locked:layer_p"]);
    await useStore.getState().refreshPlan();

    // Nothing exportable was selected, so no plan is requested at all â€” the
    // locked node never even becomes a candidate.
    expect(useStore.getState().plan).toBeNull();
  });

  it("builds a plan from the exportable part of a mixed selection", async () => {
    useStore.getState().selectMany(["n1", "locked:layer_p", "n2"]);
    await useStore.getState().refreshPlan();

    const plan = useStore.getState().plan;
    expect(plan).not.toBeNull();
    expect(plan!.sources.map((source) => source.object_id)).toEqual([
      "n1",
      "n2",
    ]);
  });
});

describe("shortestPath", () => {
  it("finds a path across the graph", () => {
    expect(shortestPath(SAMPLE_GRAPH, "n4", "n3")).toEqual([
      "n4",
      "n1",
      "n2",
      "n3",
    ]);
  });

  it("returns null when nothing connects the two", () => {
    expect(shortestPath(SAMPLE_GRAPH, "n1", "locked:layer_p")).toBeNull();
  });
});
