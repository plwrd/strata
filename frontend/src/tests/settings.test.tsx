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
      default_model: "qwythos",
      onboarding_tour_completed: true,
      minimize_to_tray: false,
      start_in_tray: false,
      hide_from_taskbar: false,
      browser_control_enabled: false,
      browser_backend: "embedded" as const,
      browser_user_scripts: [] as string[],
      browser_blocked_hosts: [] as string[],
      browser_executable_path: "",
      browser_profile_path: "",
      browser_debug_port: 9333,
      browser_search_engine: "duckduckgo",
      browser_blur_media: false,
      browser_blur_amount: 12,
      browser_mobile_mode: false,
      auto_lock_minutes: 15,
      auto_lock_on_system_lock: true,
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

  it("opens from Command bar More, without the motion toggle there", async () => {
    render(<CommandBar />);

    await userEvent.click(screen.getByRole("button", { name: "More" }));
    expect(
      screen.getByRole("button", { name: "Settings" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Motion:/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(
      screen.getByRole("dialog", { name: "Settings" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Motion preference" }),
    ).toBeInTheDocument();
  });

  it("turns off locking on system lock through applySettings", async () => {
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);
    await userEvent.click(
      screen.getByRole("checkbox", { name: /lock when Windows locks/ }),
    );
    await waitFor(() =>
      expect(applySettings).toHaveBeenCalledWith({
        auto_lock_on_system_lock: false,
      }),
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

  it("offers user scripts and a blocklist only for the built-in pane", () => {
    seedReady({ browser_control_enabled: true, browser_backend: "embedded" });
    render(<SettingsDialog onClose={() => undefined} />);

    expect(
      screen.getByRole("button", { name: /Add a user script/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Blocked hosts" }),
    ).toBeInTheDocument();
  });

  it("hides the Qt stand-ins when your own Chrome is chosen", () => {
    seedReady({ browser_control_enabled: true, browser_backend: "chrome" });
    render(<SettingsDialog onClose={() => undefined} />);

    expect(
      screen.queryByRole("button", { name: /Add a user script/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: "Blocked hosts" }),
    ).not.toBeInTheDocument();
  });

  it("turns the blocklist box into one host per line", async () => {
    seedReady({ browser_control_enabled: true, browser_backend: "embedded" });
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    const box = screen.getByRole("textbox", { name: "Blocked hosts" });
    await userEvent.click(box);
    await userEvent.paste("ads.example.com\n\n  tracker.net  \n");

    await waitFor(() =>
      expect(applySettings).toHaveBeenCalledWith({
        browser_blocked_hosts: ["ads.example.com", "tracker.net"],
      }),
    );
  });

  it("shows a user script by file name and can remove it", async () => {
    seedReady({
      browser_control_enabled: true,
      browser_backend: "embedded",
      browser_user_scripts: ["C:/scripts/dark-mode.js", "C:/scripts/reader.js"],
    });
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    expect(screen.getByText("dark-mode.js")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", {
        name: "Remove user script C:/scripts/dark-mode.js",
      }),
    );

    await waitFor(() =>
      expect(applySettings).toHaveBeenCalledWith({
        browser_user_scripts: ["C:/scripts/reader.js"],
      }),
    );
  });
});
