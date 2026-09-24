/**
 * Bridge signal subscriptions are dropped when the component that made them goes.
 *
 * This matters more than it looks. The changes panel is mounted in two places
 * (the inspector and the command stage) and remounts on every mode switch, so a
 * connect without a matching disconnect climbs for as long as the session lasts
 * — and every one of those stale listeners parses each event and is one missing
 * id-check away from acting on someone else's plan.
 */

import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { BrowserPanel } from "../features/browser/BrowserPanel";
import { OperationsPanel } from "../features/operations/OperationsPanel";
import { useStore } from "../state/store";
import {
  installFakeBridge,
  pageListeners,
  planListeners,
  PUBLIC_LAYER,
} from "./fakeBridge";

describe("bridge subscriptions", () => {
  beforeEach(() => {
    installFakeBridge();
    useStore.setState({
      connection: "ready",
      layers: [PUBLIC_LAYER],
      handedOffPlanRequestId: null,
      handedOffPlanLayerIds: [],
    });
  });

  it("drops the plan listener when the changes panel unmounts", async () => {
    const first = render(<OperationsPanel />);
    await waitFor(() => expect(planListeners.length).toBe(1));

    first.unmount();
    await waitFor(() => expect(planListeners.length).toBe(0));
  });

  it("does not accumulate listeners across remounts", async () => {
    for (let i = 0; i < 3; i += 1) {
      const view = render(<OperationsPanel />);
      await waitFor(() => expect(planListeners.length).toBe(1));
      view.unmount();
      await waitFor(() => expect(planListeners.length).toBe(0));
    }
  });

  it("keeps one listener per live panel", async () => {
    // Both mount points alive at once is a real state: the inspector and the
    // command stage each render one.
    const inspector = render(<OperationsPanel />);
    const stage = render(<OperationsPanel />);
    await waitFor(() => expect(planListeners.length).toBe(2));

    inspector.unmount();
    await waitFor(() => expect(planListeners.length).toBe(1));
    stage.unmount();
    await waitFor(() => expect(planListeners.length).toBe(0));
  });

  it("drops the page listener when the research panel unmounts", async () => {
    const view = render(<BrowserPanel />);
    await waitFor(() => expect(pageListeners.length).toBe(1));

    view.unmount();
    await waitFor(() => expect(pageListeners.length).toBe(0));
  });
});
