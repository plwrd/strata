/**
 * Bind the editor to the collaborative document when a note lives in a shared
 * layer. Returns the note's `Y.Text` and the session's awareness (for cursors),
 * or null when the layer is not shared — in which case the editor stays in its
 * ordinary local mode.
 */

import { useEffect, useState } from "react";
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
): CollabBinding | null {
  const [binding, setBinding] = useState<CollabBinding | null>(null);

  useEffect(() => {
    if (!shared || !layerId || !noteId) {
      setBinding(null);
      return;
    }
    let cancelled = false;
    void sessionFor(layerId).then((session) => {
      if (cancelled) return;
      setBinding({ text: session.text(noteId), awareness: session.awareness });
    });
    return () => {
      cancelled = true;
    };
  }, [layerId, noteId, shared]);

  return binding;
}
