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
      hide_for_sharing: true,
      minimize_to_tray: false,
      start_in_tray: false,
      hide_from_taskbar: false,
      browser_control_enabled: false,
      browser_backend: "embedded" as const,
      browser_extensions: [] as string[],
      browser_user_scripts: [] as string[],
      browser_blocked_hosts: [] as string[],
      browser_executable_path: "",
      browser_profile_path: "",
      browser_debug_port: 9333,
      browser_search_engine: "duckduckgo",
      browser_blur_media: false,
      browser_blur_amount: 12,
      browser_mobile_mode: false,
      web_archive_layer_id: "",
      web_archive_max_media_mb: 4096,
      web_archive_ffmpeg_path: "",
      web_archive_max_height: 1080,
      web_archive_allow_private_addresses: false,
      web_archive_index_text: true,
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

  it("offers only private layers for saved pages, and applies the choice", async () => {
    const layer = (id: string, visibility: "private" | "public") => ({
      id,
      display_name: `Layer ${id}`,
      visibility,
      state: "unlocked" as const,
      sharing_mode: "personal" as const,
      storage: (visibility === "private"
        ? "encrypted-objects"
        : "markdown") as never,
      storage_version: 1,
      created_at: "",
      updated_at: "",
      color: "layer-public",
      ai_policy: {} as never,
      password_remembered: false,
    });
    useStore.setState({
      layers: [layer("vault", "private"), layer("notes", "public")],
    });
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    const picker = screen.getByRole("combobox", {
      name: "Layer for saved pages",
    });
    expect(
      screen.getByRole("option", { name: "Layer vault" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Layer notes" })).toBeNull();
    await userEvent.selectOptions(picker, "vault");
    await waitFor(() =>
      expect(applySettings).toHaveBeenCalledWith({
        web_archive_layer_id: "vault",
      }),
    );
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

  // --- screen protection: the status is the OS's answer, not the toggle ------
  //
  // "Hidden for sharing" is a request. Rendering it as though it were the
  // outcome is how a user ends up screen-sharing a window they were told was
  // hidden, so each state gets its own sentence and a refusal is an alert.

  it("says the window is hidden only when the system actually hid it", () => {
    useStore.setState({ captureProtection: "excluded" });
    render(<SettingsDialog onClose={() => undefined} />);

    expect(screen.getByTestId("capture-protection")).toHaveTextContent(
      /screenshots and screen shares do not/i,
    );
  });

  it("warns when the platform has no capture control at all", () => {
    useStore.setState({ captureProtection: "unsupported" });
    render(<SettingsDialog onClose={() => undefined} />);

    const status = screen.getByTestId("capture-protection");
    expect(status).toHaveTextContent(/not available on this platform/i);
    expect(status).toHaveTextContent(
      /visible to screenshots and screen shares/i,
    );
    // And what to do instead, since there is nothing to turn on.
    expect(status).toHaveTextContent(/lock your private layers/i);
    expect(status).toHaveAttribute("role", "alert");
  });

  it("warns when the system refused to hide the window", () => {
    useStore.setState({ captureProtection: "failed" });
    render(<SettingsDialog onClose={() => undefined} />);

    const status = screen.getByTestId("capture-protection");
    expect(status).toHaveTextContent(/refused/i);
    expect(status).toHaveAttribute("role", "alert");
  });

  it("names the blackout fallback rather than calling it the same thing", () => {
    useStore.setState({ captureProtection: "blacked-out" });
    render(<SettingsDialog onClose={() => undefined} />);

    expect(screen.getByTestId("capture-protection")).toHaveTextContent(
      /black rectangle/i,
    );
  });

  it("does not claim protection while hiding is switched off", () => {
    seedReady({ hide_for_sharing: false });
    useStore.setState({ captureProtection: "off" });
    render(<SettingsDialog onClose={() => undefined} />);

    expect(screen.getByTestId("capture-protection")).toHaveTextContent(
      /appears in screenshots/i,
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
  it("keeps the extension list out of the way unless the Edge engine is on", () => {
    seedReady({ browser_control_enabled: true, browser_backend: "embedded" });
    render(<SettingsDialog onClose={() => undefined} />);

    expect(
      screen.queryByRole("button", { name: /Add an extension folder/ }),
    ).not.toBeInTheDocument();
  });

  it("lists the extension folders the Edge pane will load", () => {
    seedReady({
      browser_control_enabled: true,
      browser_backend: "webview2",
      browser_extensions: ["C:/tools/ublock", "C:/tools/reader"],
    });
    render(<SettingsDialog onClose={() => undefined} />);

    expect(screen.getByText("C:/tools/ublock")).toBeInTheDocument();
    expect(screen.getByText("C:/tools/reader")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Add an extension folder/ }),
    ).toBeInTheDocument();
  });

  it("removes one extension without disturbing the others", async () => {
    seedReady({
      browser_control_enabled: true,
      browser_backend: "webview2",
      browser_extensions: ["C:/tools/ublock", "C:/tools/reader"],
    });
    const applySettings = vi.spyOn(useStore.getState(), "applySettings");
    render(<SettingsDialog onClose={() => undefined} />);

    await userEvent.click(
      screen.getByRole("button", { name: "Remove extension C:/tools/ublock" }),
    );

    await waitFor(() =>
      expect(applySettings).toHaveBeenCalledWith({
        browser_extensions: ["C:/tools/reader"],
      }),
    );
  });

  it("survives the user cancelling the folder picker", async () => {
    seedReady({ browser_control_enabled: true, browser_backend: "webview2" });
    render(<SettingsDialog onClose={() => undefined} />);

    await userEvent.click(
      screen.getByRole("button", { name: /Add an extension folder/ }),
    );

    // Cancelling rejects; the dialog must stay usable rather than surface it.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Add an extension folder/ }),
      ).toBeInTheDocument(),
    );
  });
  it("offers user scripts and a blocklist only for the built-in pane", () => {
    // The Edge pane loads real extensions; these are what Qt has instead, and
    // showing both sets at once would imply they stack.
    seedReady({ browser_control_enabled: true, browser_backend: "embedded" });
    render(<SettingsDialog onClose={() => undefined} />);

    expect(
      screen.getByRole("button", { name: /Add a user script/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Blocked hosts" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Add an extension folder/ }),
    ).not.toBeInTheDocument();
  });

  it("hides the Qt stand-ins when the Edge engine is chosen", () => {
    seedReady({ browser_control_enabled: true, browser_backend: "webview2" });
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
