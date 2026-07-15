/**
 * The collaborative editor binding, exercised against the real CodeMirror view.
 *
 * This is the proof that the y-codemirror.next binding actually works, not just
 * that the plumbing compiles: a Y.Text mutated "remotely" appears in the editor,
 * and (via the binding) the editor and the Y.Text share one source of truth.
 */

import { render } from "@testing-library/react";
import { act } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { Awareness } from "y-protocols/awareness";
import * as Y from "yjs";
import { MarkdownEditor } from "../features/editor/MarkdownEditor";

const NOOP_SUGGESTIONS = { noteTitles: [], tags: [], propertyKeys: [] };

let doc: Y.Doc | null = null;

afterEach(() => {
  doc?.destroy();
  doc = null;
});

function bind(initial: string): { text: Y.Text; awareness: Awareness } {
  doc = new Y.Doc();
  const text = doc.getText("body");
  if (initial) text.insert(0, initial);
  return { text, awareness: new Awareness(doc) };
}

describe("collaborative editor binding", () => {
  it("renders the shared Y.Text as the editor's initial content", () => {
    const collab = bind("shared content");
    const { container } = render(
      <MarkdownEditor
        noteId="n1"
        initialContent="ignored-local-content"
        suggestions={NOOP_SUGGESTIONS}
        collab={collab}
        onChange={() => undefined}
        onSave={() => undefined}
      />,
    );
    // The editor shows the Y.Text, not the local initialContent.
    expect(container.querySelector(".cm-content")?.textContent).toContain(
      "shared content",
    );
  });

  it("reflects a remote Y.Text edit in the editor", () => {
    const collab = bind("hello");
    const { container } = render(
      <MarkdownEditor
        noteId="n1"
        initialContent=""
        suggestions={NOOP_SUGGESTIONS}
        collab={collab}
        onChange={() => undefined}
        onSave={() => undefined}
      />,
    );

    // A "remote" peer appends to the shared text.
    act(() => {
      collab.text.insert(collab.text.length, " world");
    });

    expect(container.querySelector(".cm-content")?.textContent).toContain(
      "hello world",
    );
  });

  it("still surfaces edits to onChange so the store stays consistent", () => {
    const collab = bind("x");
    let latest = "";
    render(
      <MarkdownEditor
        noteId="n1"
        initialContent=""
        suggestions={NOOP_SUGGESTIONS}
        collab={collab}
        onChange={(value) => {
          latest = value;
        }}
        onSave={() => undefined}
      />,
    );
    act(() => {
      collab.text.insert(collab.text.length, "yz");
    });
    // The merged content flows to onChange (which drives the draft + autosave),
    // so search/graph/export see the collaborative edits too.
    expect(latest).toContain("xyz");
  });
});
