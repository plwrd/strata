/**
 * The note's version trail: every mutation, who caused it, and a restore.
 *
 * Restore is never a silent overwrite — Python captures the current state as a
 * new version first, so restoring is always itself reversible. Private-layer
 * notes report `supported: false` (no plaintext history on disk) and the panel
 * says so instead of pretending there is nothing to show.
 */

import { useCallback, useEffect, useState } from "react";
import { bridge } from "../../bridge/client";
import type { NoteVersionSummary } from "../../bridge/types";
import { useStore } from "../../state/store";

type LoadState =
  | { kind: "loading" }
  | {
      kind: "ready";
      versions: NoteVersionSummary[];
      supported: boolean;
      detail: string;
    }
  | { kind: "error"; message: string };

function formatTime(iso: string): string {
  const time = new Date(iso);
  return Number.isNaN(time.getTime()) ? iso : time.toLocaleString();
}

function originLabel(origin: string): string {
  if (origin.startsWith("ai:")) return "AI";
  if (origin === "restore") return "restore";
  return "you";
}

export function VersionHistory(): JSX.Element | null {
  const { openNote, openNoteById } = useStore();
  const noteId = openNote?.metadata.id ?? null;
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [confirming, setConfirming] = useState<number | null>(null);

  const load = useCallback(async (id: string) => {
    setState({ kind: "loading" });
    try {
      const response = await bridge.notes.listVersions(id);
      setState({
        kind: "ready",
        versions: response.versions,
        supported: response.supported,
        detail: response.detail,
      });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof Error
            ? error.message
            : "Could not load the history.",
      });
    }
  }, []);

  useEffect(() => {
    setConfirming(null);
    if (noteId) void load(noteId);
  }, [noteId, openNote?.metadata.updated_at, load]);

  if (!noteId) return null;

  const restore = async (index: number): Promise<void> => {
    setConfirming(null);
    try {
      await bridge.notes.restoreVersion(noteId, index);
      // Reload the note so the editor shows the restored state.
      await openNoteById(noteId);
      await load(noteId);
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "Restore failed.",
      });
    }
  };

  return (
    <section className="versions" aria-label="Version history">
      <h3 className="sidebar__heading">History</h3>

      {state.kind === "loading" && (
        <p className="composer__status" role="status">
          Loading history…
        </p>
      )}

      {state.kind === "error" && (
        <p className="composer__status composer__status--error" role="alert">
          {state.message}
        </p>
      )}

      {state.kind === "ready" && !state.supported && (
        <p className="empty-state">{state.detail}</p>
      )}

      {state.kind === "ready" &&
        state.supported &&
        state.versions.length === 0 && (
          <p className="empty-state">
            No earlier versions yet. Every future edit will leave one.
          </p>
        )}

      {state.kind === "ready" &&
        state.supported &&
        state.versions.length > 0 && (
          <ul className="versions__list">
            {state.versions.map((version) => (
              <li key={version.index} className="versions__item">
                <div className="versions__meta">
                  <span
                    className={`tag ${version.origin.startsWith("ai:") ? "tag--ai" : ""}`}
                  >
                    {originLabel(version.origin)}
                  </span>
                  <span className="mono versions__change">
                    {version.change}
                  </span>
                  <time className="versions__time">
                    {formatTime(version.created_at)}
                  </time>
                </div>
                {confirming === version.index ? (
                  <div className="versions__confirm">
                    <span>Restore this state?</span>
                    <button
                      type="button"
                      className="button button--primary"
                      onClick={() => void restore(version.index)}
                    >
                      Restore
                    </button>
                    <button
                      type="button"
                      className="button button--ghost"
                      onClick={() => setConfirming(null)}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="button button--ghost"
                    onClick={() => setConfirming(version.index)}
                  >
                    Restore…
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}
