/**
 * Bind the editor to the collaborative document when a note lives in a shared
 * layer. Returns the note's `Y.Text` and the session's awareness (for cursors),
 * or null when the layer is not shared — in which case the editor stays in its
 * ordinary local mode.
 */

import { useEffect, useRef, useState } from "react";
import type { Awareness } from "y-protocols/awareness";
import type * as Y from "yjs";
import { sessionFor } from "./collabDoc";

export interface CollabBinding {
  text: Y.Text;
  awareness: Awareness;
}

export function useCollabText(
  layerId: string | null,
  noteId: string | null,
  shared: boolean,
  initialContent: string,
): CollabBinding | null {
  const [binding, setBinding] = useState<CollabBinding | null>(null);
  // Read the latest content without making it a hook dependency (re-binding on
  // every keystroke would be wrong); only the identity of the note matters.
  const contentRef = useRef(initialContent);
  contentRef.current = initialContent;

  useEffect(() => {
    if (!shared || !layerId || !noteId) {
      setBinding(null);
      return;
    }
    let cancelled = false;
    sessionFor(layerId)
      .then((session) => {
        if (cancelled) return;
        setBinding({
          text: session.ensureText(noteId, contentRef.current),
          awareness: session.awareness,
        });
      })
      .catch(() => {
        // Connect failed; stay in ordinary local mode rather than crash. A later
        // attempt can retry (the poisoned-promise cache was already cleared).
        if (!cancelled) setBinding(null);
      });
    return () => {
      cancelled = true;
    };
  }, [layerId, noteId, shared]);

  return binding;
}
