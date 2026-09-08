/**
 * Browser research — the panel's contract with the backend.
 *
 * What matters here is the shape of the flow, not the styling: a search is a
 * navigation the *browser* performs, scraping writes nothing, reads arrive
 * asynchronously on `pageEvent` and are matched by request id, filing scopes
 * the request to the layers the user actually ticked, and the resulting plan is
 * handed to the Changes panel instead of being applied here.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { BrowserPanel } from "../features/browser/BrowserPanel";
import { useStore } from "../state/store";
import {
  captured,
  installFakeBridge,
  PUBLIC_LAYER,
  PRIVATE_LAYER,
} from "./fakeBridge";

function seedLayers(): void {
  useStore.setState({
    connection: "ready",
    layers: [PUBLIC_LAYER, { ...PRIVATE_LAYER, state: "unlocked" }],
    providerId: "ollama",
    model: "qwythos",
    handedOffPlanRequestId: null,
    handedOffPlanLayerIds: [],
  });
}

describe("BrowserPanel", () => {
  beforeEach(() => {
    installFakeBridge();
    seedLayers();
  });

  it("sends a search to the browser rather than fetching it", async () => {
    render(<BrowserPanel />);
    await screen.findByText(/pane is closed/);

    await userEvent.type(
      screen.getByRole("searchbox", { name: "Search the web" }),
      "vector index tradeoffs",
    );
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      const payload = captured.find((entry) => "query" in entry);
      expect(payload?.["query"]).toBe("vector index tradeoffs");
    });
  });

  it("scrapes without writing a note", async () => {
    render(<BrowserPanel />);
    await screen.findByText(/pane is closed/);

    await userEvent.click(
      screen.getByRole("button", { name: "Open browser pane" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Scrape page" }),
    );

    // The preview shows the page, and shows it as text.
    expect(await screen.findByText("Scraped body text.")).toBeInTheDocument();
    expect(screen.getByText("18 characters")).toBeInTheDocument();
    // Nothing was captured: scraping is a look, not a write.
    expect(captured.some((entry) => "capture_reason" in entry)).toBe(false);
  });

  it("files research scoped to the ticked layers and hands the plan over", async () => {
    render(<BrowserPanel />);
    await screen.findByText(/pane is closed/);
    await userEvent.click(
      screen.getByRole("button", { name: "Open browser pane" }),
    );

    // Untick the private layer: it must not appear in the request's scope.
    await userEvent.click(
      screen.getByRole("checkbox", { name: /Deals \(private\)/ }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Analyse & file" }),
    );

    await waitFor(() => {
      const payload = captured.find((entry) => "layer_ids" in entry);
      expect(payload?.["layer_ids"]).toEqual([PUBLIC_LAYER.id]);
      expect(payload?.["note_ids"]).toEqual(["note_capture_1"]);
    });
    // The plan is reviewed in Changes, never applied from this panel.
    await waitFor(() =>
      expect(useStore.getState().handedOffPlanRequestId).toBe("req_research_1"),
    );
    expect(useStore.getState().handedOffPlanLayerIds).toEqual([
      PUBLIC_LAYER.id,
    ]);
  });

  it("offers a tab list for Chrome and not for the pane", async () => {
    // The pane shows one page and has its own address bar, so a tab list there
    // would be a second, lying copy of it.
    render(<BrowserPanel />);
    await screen.findByText(/pane is closed/);
    await userEvent.click(
      screen.getByRole("button", { name: "Open browser pane" }),
    );
    expect(
      screen.queryByRole("combobox", { name: "Tab to read" }),
    ).not.toBeInTheDocument();

    installFakeBridge({ browserBackend: "chrome" });
    seedLayers();
    render(<BrowserPanel />);
    const opens = await screen.findAllByRole("button", {
      name: "Open browser",
    });
    await userEvent.click(opens[0]!);

    expect(
      await screen.findByRole("combobox", { name: "Tab to read" }),
    ).toBeInTheDocument();
  });

  it("explains itself instead of failing when the feature is off", async () => {
    installFakeBridge({ browserEnabled: false });
    seedLayers();
    render(<BrowserPanel />);

    expect(
      await screen.findByText(/Browser research is off/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Analyse & file" }),
    ).not.toBeInTheDocument();
  });
});
