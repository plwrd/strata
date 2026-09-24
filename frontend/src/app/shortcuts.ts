/**
 * Global keyboard shortcuts.
 *
 * A module rather than a closure inside `App`, so the mapping can be tested for
 * what it is — "this chord does that" — without mounting the whole shell.
 *
 * Everything here is Ctrl/Cmd-prefixed. Qt WebEngine has no browser chrome to
 * fight over the keys, but we still `preventDefault` so nothing else claims
 * them, and we only ever act on chords we actually handle.
 */

import type { StrataState } from "../state/store";

/** Handle one keydown. Returns true when the chord was ours. */
export function handleGlobalShortcut(
  event: KeyboardEvent,
  store: StrataState,
): boolean {
  if (!(event.ctrlKey || event.metaKey)) return false;
  const key = event.key.toLowerCase();

  // Ctrl/Cmd+N — new note in the first unlocked layer.
  if (key === "n" && !event.shiftKey) {
    const target = store.layers.find((layer) => layer.state !== "locked");
    if (!target) return false;
    event.preventDefault();
    void store.createNote(target.id, "");
    return true;
  }

  // Ctrl/Cmd+W — close the active editor tab.
  if (key === "w" && !event.shiftKey) {
    if (!store.activeNoteId || store.tabs.length === 0) return false;
    event.preventDefault();
    store.closeTab(store.activeNoteId);
    return true;
  }

  // Ctrl/Cmd+Shift+T — reopen the most recently closed tab.
  if (key === "t" && event.shiftKey) {
    event.preventDefault();
    void store.reopenClosedTab();
    return true;
  }

  // Ctrl/Cmd+, — Settings. The platform convention, and the way to reach every
  // option that does not have a key of its own.
  if (key === ",") {
    event.preventDefault();
    store.setSettingsOpen(!store.settingsOpen);
    return true;
  }

  // Ctrl/Cmd+Shift+H — "Hidden for sharing". Someone asking you to share your
  // screen is exactly the moment you cannot afford to go hunting for a
  // checkbox, so this one skips the dialog entirely.
  if (key === "h" && event.shiftKey) {
    event.preventDefault();
    void store.applySettings({
      hide_for_sharing: !(store.settings?.hide_for_sharing ?? true),
    });
    return true;
  }

  // Ctrl/Cmd+Shift+B — the research browser pane, open or closed. It takes half
  // the window, so it wants a key rather than a trip through the navigator.
  if (key === "b" && event.shiftKey) {
    event.preventDefault();
    void store.toggleBrowserPane();
    return true;
  }

  // Ctrl/Cmd+Shift+X — blur the media in the research pane.
  //
  // Handled here *as well as* by the Qt application shortcut, and that is the
  // fix rather than a duplicate: Qt WebEngine claims a chord for the page
  // whenever the focus is on an editable element, so the native shortcut went
  // missing exactly when someone was typing — which the guide promises is the
  // one case it works in. A keydown on `window` sees it either way.
  if (key === "x" && event.shiftKey) {
    event.preventDefault();
    void store.toggleBrowserBlur();
    return true;
  }

  return false;
}
