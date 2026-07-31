/**
 * Settings dialog: appearance theme and display prefs already in AppSettings.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsDialog } from "../features/settings/SettingsDialog";
import { CommandBar } from "../features/workspace/CommandBar";
import { useStore } from "../state/store";
import { installFakeBridge } from "./fakeBridge";

function seedReady(): void {
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
      default_provider: "ollama",
      default_model: "deepseek-r1:7b",
      onboarding_tour_completed: true,
      hide_for_sharing: false,
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

describe("SettingsDialog", () => {
  beforeEach(() => {
    installFakeBridge();
    seedReady();
  });

  it("exposes appearance and applies a theme change", async () => {
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    expect(screen.getByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cyberpunk Dark" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await userEvent.click(screen.getByRole("button", { name: "High contrast" }));
    expect(applySettings).toHaveBeenCalledWith({ appearance: "high-contrast" });
  });

  it("opens from Command bar More, without motion/sharing toggles there", async () => {
    render(<CommandBar />);

    await userEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
    expect(screen.queryByText(/Motion:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Hidden for sharing/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Hidden for sharing")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Motion preference" })).toBeInTheDocument();
  });

  it("toggles hide-for-sharing through applySettings", async () => {
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    await userEvent.click(screen.getByRole("checkbox", { name: /Hidden for sharing/ }));
    await waitFor(() =>
      expect(applySettings).toHaveBeenCalledWith({ hide_for_sharing: true }),
    );
  });
});
