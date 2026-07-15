/**
 * Accessibility smoke tests (M11, NFR-030..033).
 *
 * axe-core in jsdom cannot judge colour contrast or focus order (no layout), but
 * it does catch the failures that make a control unreachable to assistive tech:
 * a button with no accessible name, an input with no label, a bad ARIA role, a
 * list structure broken. These panels are the ones this session added, so they
 * are the ones most worth pinning.
 */

import { render } from "@testing-library/react";
import { axe } from "vitest-axe";
import * as axeMatchers from "vitest-axe/matchers";
import { beforeEach, describe, expect, it } from "vitest";
import { CollaborationPanel } from "../features/collaboration/CollaborationPanel";
import { GraphControls } from "../features/graph/GraphControls";
import { useStore } from "../state/store";
import { installFakeBridge, SAMPLE_GRAPH } from "./fakeBridge";

expect.extend(axeMatchers);

function readableLayer() {
  return {
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
  };
}

describe("accessibility", () => {
  beforeEach(() => {
    installFakeBridge();
  });

  it("the collaboration panel has no axe violations", async () => {
    useStore.setState({
      layers: [readableLayer()] as never,
      collab: {},
      collabConflicts: {},
    });
    const { container } = render(<CollaborationPanel />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("the graph controls have no axe violations", async () => {
    useStore.setState({
      graph: SAMPLE_GRAPH,
      dimension: "2d",
      selectedIds: ["n1"],
      lastAnchorId: "n1",
      semanticEdges: false,
      clusterColors: false,
    });
    const { container } = render(<GraphControls />);
    expect(await axe(container)).toHaveNoViolations();
  });
});
