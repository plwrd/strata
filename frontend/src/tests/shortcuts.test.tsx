/**
 * Global shortcuts for the settings people actually reach for.
 *
 * The mapping is tested against the real store, so a shortcut that calls the
 * wrong action fails here.
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
      settings: {} as never,
    });
  });

  it("opens and closes Settings with Ctrl+,", () => {
    expect(press(",")).toBe(true);
    expect(useStore.getState().settingsOpen).toBe(true);

    expect(press(",")).toBe(true);
    expect(useStore.getState().settingsOpen).toBe(false);
  });

  it("leaves Ctrl+Shift+H unbound", () => {
    expect(press("H", { shift: true })).toBe(false);
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

  // Ctrl/Cmd+Shift+X — blur the research pane's media.
  //
  // This lives in the web layer as well as in Qt, and that duplication is the
  // fix: Qt WebEngine claims a chord for the page whenever an editable element
  // has focus, so the native shortcut went missing exactly while someone was
  // typing — the case the guide promises it works in.

  it("toggles media blur with Ctrl+Shift+X", async () => {
    expect(press("X", { shift: true })).toBe(true);

    await waitFor(() => {
      const payload = captured.find(
        (entry) => entry["method"] === "toggle_blur",
      );
      expect(payload).toBeDefined();
    });
    expect(useStore.getState().lastError).toBeNull();
  });

  it("flips blur at the source rather than setting the opposite", async () => {
    // Two presses land back where they started. A read-modify-write from a
    // stale copy is how a hotkey press and a panel click cancelled out.
    expect(press("X", { shift: true })).toBe(true);
    await waitFor(() =>
      expect(
        captured.filter((entry) => entry["method"] === "toggle_blur"),
      ).toHaveLength(1),
    );

    expect(press("X", { shift: true })).toBe(true);
    await waitFor(() =>
      expect(
        captured.filter((entry) => entry["method"] === "toggle_blur"),
      ).toHaveLength(2),
    );
    // Never a set with a computed value.
    expect(captured.some((entry) => "enabled" in entry)).toBe(false);
  });

  it("says why when there is nothing to blur, instead of nothing", async () => {
    installFakeBridge({ browserBackend: "chrome" });

    expect(press("X", { shift: true })).toBe(true);

    await waitFor(() =>
      expect(useStore.getState().lastError).toMatch(/built-in pane/i),
    );
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
