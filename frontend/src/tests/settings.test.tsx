/**
 * Settings dialog: templates, typography, colour overrides, chrome prefs.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsDialog } from "../features/settings/SettingsDialog";
import { CommandBar } from "../features/workspace/CommandBar";
import { useStore } from "../state/store";
import { installFakeBridge } from "./fakeBridge";

function seedReady(overrides: Record<string, unknown> = {}): void {
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
      font_body: "inter",
      font_display: "chakra",
      font_mono: "jetbrains",
      ui_scale: 1,
      theme_colors: {},
      relay_url: "",
      default_provider: "ollama",
      default_model: "deepseek-r1:7b",
      onboarding_tour_completed: true,
      hide_for_sharing: true,
      ...overrides,
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

  it("exposes templates and clears colours when switching pack", async () => {
    seedReady({
      theme_colors: { accent_primary: "#ff0000" },
    });
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    expect(
      screen.getByRole("dialog", { name: "Settings" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Customized")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Forest/i }));
    expect(applySettings).toHaveBeenCalledWith({
      appearance: "forest",
      theme_colors: {},
    });
  });

  it("applies typography and UI scale", async () => {
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Body font" }),
      "system",
    );
    expect(applySettings).toHaveBeenCalledWith({ font_body: "system" });

    await userEvent.click(screen.getByRole("button", { name: "Large" }));
    expect(applySettings).toHaveBeenCalledWith({ ui_scale: 1.1 });
  });

  it("commits a colour override via hex field", async () => {
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    const hex = screen.getByRole("textbox", { name: "Connected edge hex" });
    await userEvent.clear(hex);
    await userEvent.type(hex, "#aabbcc");
    await userEvent.tab();

    await waitFor(() =>
      expect(applySettings).toHaveBeenCalledWith({
        theme_colors: { graph_edge_selected: "#aabbcc" },
      }),
    );
  });

  it("opens from Command bar More, without motion/sharing toggles there", async () => {
    render(<CommandBar />);

    await userEvent.click(screen.getByRole("button", { name: "More" }));
    expect(
      screen.getByRole("button", { name: "Settings" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Motion:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Hidden for sharing/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(
      screen.getByRole("dialog", { name: "Settings" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Hidden for sharing")).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Motion preference" }),
    ).toBeInTheDocument();
  });

  it("toggles hide-for-sharing through applySettings", async () => {
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    await userEvent.click(
      screen.getByRole("checkbox", { name: /Hidden for sharing/ }),
    );
    await waitFor(() =>
      expect(applySettings).toHaveBeenCalledWith({ hide_for_sharing: false }),
    );
  });

  it("toggles battery saver through applySettings", async () => {
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    await userEvent.click(
      screen.getByRole("checkbox", { name: /Battery saver/ }),
    );
    await waitFor(() =>
      expect(applySettings).toHaveBeenCalledWith({ battery_saver: true }),
    );
  });
});
