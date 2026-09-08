/**
 * Global shortcuts for the settings people actually reach for.
 *
 * The mapping is tested against the real store, so a shortcut that calls the
 * wrong action fails here. `Ctrl/Cmd+Shift+H` matters most — it is the one you
 * press because someone just asked to see your screen, and it has to work
 * without opening anything.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { handleGlobalShortcut } from "../app/shortcuts";
import { useStore } from "../state/store";
import { captured, installFakeBridge } from "./fakeBridge";

function press(key: string, modifiers: { shift?: boolean } = {}): boolean {
  const event = new KeyboardEvent("keydown", {
    key,
    ctrlKey: true,
    shiftKey: modifiers.shift ?? false,
    cancelable: true,
  });
  const handled = handleGlobalShortcut(event, useStore.getState());
  return handled && event.defaultPrevented;
}

describe("global shortcuts", () => {
  beforeEach(() => {
    installFakeBridge();
    useStore.setState({
      connection: "ready",
      settingsOpen: false,
      browserRevision: 0,
      lastError: null,
      settings: { hide_for_sharing: true } as never,
    });
  });

  it("opens and closes Settings with Ctrl+,", () => {
    expect(press(",")).toBe(true);
    expect(useStore.getState().settingsOpen).toBe(true);

    expect(press(",")).toBe(true);
    expect(useStore.getState().settingsOpen).toBe(false);
  });

  it("toggles Hidden for sharing without opening a dialog", async () => {
    expect(press("H", { shift: true })).toBe(true);

    await waitFor(() =>
      expect(useStore.getState().settings?.hide_for_sharing).toBe(false),
    );
    const payload = captured.find(
      (entry) =>
        "values" in entry &&
        "hide_for_sharing" in (entry["values"] as Record<string, unknown>),
    );
    expect(payload).toBeDefined();
    // The dialog never opened: this is the whole point of the shortcut.
    expect(useStore.getState().settingsOpen).toBe(false);
  });

  it("opens the research browser pane with Ctrl+Shift+B", async () => {
    expect(press("B", { shift: true })).toBe(true);

    // The pane is a native widget, so the proof is that Python was asked for it
    // — and that the Research panel is told to re-read a status it did not set.
    await waitFor(() =>
      expect(useStore.getState().browserRevision).toBeGreaterThan(0),
    );
  });

  it("says where the switch is when research is off, instead of failing", async () => {
    installFakeBridge({ browserEnabled: false });
    useStore.setState({ browserRevision: 0, lastError: null });

    press("B", { shift: true });

    await waitFor(() =>
      expect(useStore.getState().lastError).toMatch(/Settings/),
    );
    expect(useStore.getState().browserRevision).toBe(0);
  });

  it("leaves chords it does not own alone", () => {
    const event = new KeyboardEvent("keydown", {
      key: "k",
      ctrlKey: true,
      cancelable: true,
    });
    expect(handleGlobalShortcut(event, useStore.getState())).toBe(false);
    expect(event.defaultPrevented).toBe(false);

    // No modifier: a plain comma is someone typing, not a shortcut.
    const typing = new KeyboardEvent("keydown", { key: ",", cancelable: true });
    expect(handleGlobalShortcut(typing, useStore.getState())).toBe(false);
    expect(useStore.getState().settingsOpen).toBe(false);
  });

  it("does nothing on Ctrl+W when no tab is open", () => {
    useStore.setState({ activeNoteId: null, tabs: [] });
    const closeTab = vi.spyOn(useStore.getState(), "closeTab");

    expect(press("w")).toBe(false);
    expect(closeTab).not.toHaveBeenCalled();
  });
});
