/**
 * First-run interactive tutorial: auto-start, skip persistence, replay, mode switch.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OnboardingTour } from "../features/onboarding/OnboardingTour";
import { registerShellChrome } from "../features/onboarding/shellChrome";
import { requestTourReplay } from "../features/onboarding/useOnboardingTour";
import { CommandBar } from "../features/workspace/CommandBar";
import { useStore } from "../state/store";
import { installFakeBridge } from "./fakeBridge";

function seedSettings(completed: boolean): void {
  useStore.setState({
    connection: "ready",
    settings: {
      format_version: 1,
      appearance: "cyberpunk-dark",
      motion: "system",
      graph_quality: "balanced",
      particles_enabled: true,
      bloom_enabled: true,
      battery_saver: false,
      telemetry_enabled: false,
      default_lens_id: "lens_all",
      last_workspace_path: "",
      developer_tools: false,
      relay_url: "",
      onboarding_tour_completed: completed,
    },
    mode: "explore",
    workspace: {
      is_open: true,
      workspace: {
        format_version: 1,
        id: "ws_test",
        name: "Test",
        created_at: "",
        updated_at: "",
        layer_order: [],
        layers: [],
        lenses: [],
      },
      lenses: [],
    },
    activeLensId: "lens_all",
    tree: { folders: [], notes: [], locked_layer_ids: [] },
    graph: null,
    layers: [],
  });
}

function TourHarness(): JSX.Element {
  return (
    <div>
      <div data-tour="modes">modes</div>
      <div data-tour="capture">capture</div>
      <div data-tour="layers">layers</div>
      <div data-tour="files">files</div>
      <div data-tour="graph">graph</div>
      <div data-tour="inspector-ai">ai</div>
      <OnboardingTour />
    </div>
  );
}

describe("Onboarding tour", () => {
  beforeEach(() => {
    installFakeBridge();
    registerShellChrome({
      setNavOpen: vi.fn(),
      setInspectorOpen: vi.fn(),
      setInspectorTab: vi.fn(),
    });
  });

  it("auto-opens the welcome dialog when the tour is not completed", async () => {
    seedSettings(false);
    render(<TourHarness />);

    expect(
      await screen.findByRole("dialog", { name: /Welcome to your workspace/i }),
    ).toBeInTheDocument();
  });

  it("does not auto-open when the tour was already completed", () => {
    seedSettings(true);
    render(<TourHarness />);

    expect(
      screen.queryByRole("dialog", { name: /Welcome to your workspace/i }),
    ).not.toBeInTheDocument();
  });

  it("Skip marks the tour completed via settings", async () => {
    seedSettings(false);
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<TourHarness />);

    await screen.findByRole("dialog", { name: /Welcome to your workspace/i });
    await userEvent.click(screen.getByRole("button", { name: /^Skip$/i }));

    await waitFor(() => {
      expect(applySettings).toHaveBeenCalledWith({
        onboarding_tour_completed: true,
      });
    });
    expect(
      screen.queryByRole("dialog", { name: /Welcome to your workspace/i }),
    ).not.toBeInTheDocument();
  });

  it("advances into spotlight and switches mode on the writing step", async () => {
    seedSettings(false);
    render(<TourHarness />);

    await screen.findByRole("dialog", { name: /Welcome to your workspace/i });
    await userEvent.click(screen.getByRole("button", { name: /Start tour/i }));

    expect(
      await screen.findByRole("heading", { name: /Four ways to work/i }),
    ).toBeInTheDocument();

    // modes → capture → layers → writing (focus)
    await userEvent.click(screen.getByRole("button", { name: /^Next$/i }));
    await userEvent.click(screen.getByRole("button", { name: /^Next$/i }));
    await userEvent.click(screen.getByRole("button", { name: /^Next$/i }));

    expect(
      await screen.findByRole("heading", { name: /Your notes, your files/i }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(useStore.getState().mode).toBe("focus");
    });
  });

  it("More → Tutorial replays after completion", async () => {
    seedSettings(true);
    render(
      <>
        <CommandBar />
        <TourHarness />
      </>,
    );

    expect(
      screen.queryByRole("dialog", { name: /Welcome to your workspace/i }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^More$/i }));
    const menu = screen.getByRole("group", { name: /More workspace controls/i });
    await userEvent.click(within(menu).getByRole("button", { name: /Tutorial/i }));

    expect(
      await screen.findByRole("dialog", { name: /Welcome to your workspace/i }),
    ).toBeInTheDocument();
  });

  it("requestTourReplay opens the welcome dialog", async () => {
    seedSettings(true);
    render(<TourHarness />);
    await act(async () => {
      requestTourReplay();
    });

    expect(
      await screen.findByRole("dialog", { name: /Welcome to your workspace/i }),
    ).toBeInTheDocument();
  });
});
