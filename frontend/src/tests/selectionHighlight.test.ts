import { describe, expect, it } from "vitest";
import { edgeIsLit, neighborIds } from "../features/graph/nodeStyle";

describe("selection neighbourhood", () => {
  const edges = [
    { source: "a", target: "b" },
    { source: "b", target: "c" },
    { source: "d", target: "e" },
  ];

  it("lists one-hop neighbours of the selection", () => {
    expect([...neighborIds(edges, new Set(["a"]))].sort()).toEqual(["b"]);
    expect([...neighborIds(edges, new Set(["b"]))].sort()).toEqual(["a", "c"]);
  });

  it("does not include selected nodes as neighbours", () => {
    expect([...neighborIds(edges, new Set(["a", "b"]))].sort()).toEqual(["c"]);
  });

  it("lights every edge that touches the selection", () => {
    const selected = new Set(["a"]);
    expect(edgeIsLit(edges[0]!, selected)).toBe(true);
    expect(edgeIsLit(edges[1]!, selected)).toBe(false);
    expect(edgeIsLit(edges[2]!, selected)).toBe(false);
  });
});
