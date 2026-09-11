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
  blurListeners,
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
    // Two steps on purpose: the first click opens the options, the second runs.
    await userEvent.click(
      await screen.findByRole("button", { name: /Analyse & file/ }),
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Analysis focus" }),
      "pricing and limits",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Start analysis" }),
    );

    await waitFor(() => {
      const payload = captured.find((entry) => "layer_ids" in entry);
      expect(payload?.["layer_ids"]).toEqual([PUBLIC_LAYER.id]);
      expect(payload?.["note_ids"]).toEqual(["note_capture_1"]);
      expect(payload?.["focus"]).toBe("pricing and limits");
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

  it("blurs media and stays in step with the hotkey", async () => {
    render(<BrowserPanel />);
    await screen.findByText(/pane is closed/);
    await userEvent.click(
      screen.getByRole("button", { name: "Open browser pane" }),
    );

    const button = await screen.findByRole("button", { name: "Blur media" });
    await userEvent.click(button);

    // The click asked Python to *flip* it, rather than to set the opposite of
    // what this component last saw — the panel and the hotkey change the same
    // state, and two "make it the opposite" calls cancel out.
    await waitFor(() => {
      const payload = captured.find(
        (entry) => entry["method"] === "toggle_blur",
      );
      expect(payload).toBeDefined();
    });
    expect(
      await screen.findByRole("button", { name: "Media blurred" }),
    ).toBeInTheDocument();

    // A hotkey toggle Python pushes over blurEvent flips the button back, even
    // though this panel never made the change.
    for (const listener of blurListeners) {
      listener(JSON.stringify({ enabled: false, amount: 12, supported: true }));
    }
    expect(
      await screen.findByRole("button", { name: "Blur media" }),
    ).toBeInTheDocument();
  });

  it("digests the page instead of saving it whole when asked", async () => {
    render(<BrowserPanel />);
    await screen.findByText(/pane is closed/);
    await userEvent.click(
      screen.getByRole("button", { name: "Open browser pane" }),
    );

    // Pick a brief, add a focus and tags — the "manage and sort" controls.
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Capture mode" }),
      "brief",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Digest focus" }),
      "pricing",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Capture tags" }),
      "vectors, pricing",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Digest & capture" }),
    );

    await waitFor(() => {
      const payload = captured.find((entry) => "mode" in entry);
      expect(payload?.["mode"]).toBe("brief");
      expect(payload?.["instruction"]).toBe("pricing");
      expect(payload?.["tags"]).toEqual(["vectors", "pricing"]);
    });
    // The preview shows the digest that was kept, not the whole page.
    expect(await screen.findByText(/A short brief/)).toBeInTheDocument();
  });

  it("opens the current page in the real browser for video", async () => {
    render(<BrowserPanel />);
    await screen.findByText(/pane is closed/);
    await userEvent.click(
      screen.getByRole("button", { name: "Open browser pane" }),
    );
    // A scrape gives the panel a current URL to hand off.
    await userEvent.click(
      await screen.findByRole("button", { name: "Scrape page" }),
    );
    await screen.findByText("Scraped body text.");

    await userEvent.click(
      await screen.findByRole("button", { name: "Open in browser" }),
    );

    await waitFor(() => {
      const payload = captured.find(
        (entry) => "url" in entry && !("layer_id" in entry),
      );
      expect(payload?.["url"]).toBe("https://example.com/paper");
    });
  });

  it("warns that the embedded pane cannot play H.264 video", async () => {
    render(<BrowserPanel />);
    await screen.findByText(/pane is closed/);
    await userEvent.click(
      screen.getByRole("button", { name: "Open browser pane" }),
    );
    expect(await screen.findByText(/not H\.264/)).toBeInTheDocument();
  });

  it("does not show the H.264 warning for the Chrome backend", async () => {
    installFakeBridge({ browserBackend: "chrome" });
    seedLayers();
    render(<BrowserPanel />);
    const opens = await screen.findAllByRole("button", {
      name: "Open browser",
    });
    await userEvent.click(opens[0]!);
    expect(screen.queryByText(/not H\.264/)).not.toBeInTheDocument();
  });

  it("switches the pane to a mobile layout", async () => {
    render(<BrowserPanel />);
    await screen.findByText(/pane is closed/);
    await userEvent.click(
      screen.getByRole("button", { name: "Open browser pane" }),
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "Desktop site" }),
    );

    await waitFor(() => {
      const payload = captured.find(
        (entry) => "enabled" in entry && !("amount" in entry),
      );
      // set_mobile was asked to turn on.
      expect(
        captured.some((e) => "enabled" in e && e["enabled"] === true),
      ).toBe(true);
      void payload;
    });
    expect(
      await screen.findByRole("button", { name: "Mobile site" }),
    ).toBeInTheDocument();
  });

  it("hides the blur control for the Chrome backend", async () => {
    installFakeBridge({ browserBackend: "chrome" });
    seedLayers();
    render(<BrowserPanel />);
    const opens = await screen.findAllByRole("button", {
      name: "Open browser",
    });
    await userEvent.click(opens[0]!);

    expect(
      screen.queryByRole("button", { name: "Blur media" }),
    ).not.toBeInTheDocument();
  });

  it("explains itself instead of failing when the feature is off", async () => {
    installFakeBridge({ browserEnabled: false });
    seedLayers();
    render(<BrowserPanel />);

    expect(
      await screen.findByText(/Browser research is off/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Analyse & file/ }),
    ).not.toBeInTheDocument();
  });
  it("asks before it analyses, instead of starting on the first click", async () => {
    // Analysing costs a model call and a review; the click used to start one
    // with no chance to steer it.
    installFakeBridge();
    seedLayers();
    render(<BrowserPanel />);

    await userEvent.click(
      screen.getByRole("button", { name: "Open browser pane" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Analyse & file/ }),
    );

    expect(
      screen.getByRole("textbox", { name: "Analysis focus" }),
    ).toBeInTheDocument();
    expect(captured.find((entry) => "layer_ids" in entry)).toBeUndefined();

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(
      screen.queryByRole("textbox", { name: "Analysis focus" }),
    ).not.toBeInTheDocument();
    expect(captured.find((entry) => "layer_ids" in entry)).toBeUndefined();
  });
});
