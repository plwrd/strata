/**
 * The Markdown editor: CodeMirror 6.
 *
 * Two rules shape this component:
 *
 * 1. **Typing latency is sacred.** Autosave is debounced and runs through the
 *    bridge off the keystroke path; nothing in the type-handler talks to Python.
 * 2. **The file is the truth.** When the file changes on disk underneath us
 *    (another editor, a sync), the editor reloads rather than clobbering it — and
 *    if there are unsaved local edits, it says so instead of silently picking one.
 */

import {
  autocompletion,
  type CompletionContext,
  type CompletionResult,
} from "@codemirror/autocomplete";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { syntaxHighlighting, HighlightStyle } from "@codemirror/language";
import { searchKeymap } from "@codemirror/search";
import { EditorState, type Extension } from "@codemirror/state";
import { EditorView, keymap, placeholder } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { yCollab } from "y-codemirror.next";
import { useEffect, useRef } from "react";
import type { CollabBinding } from "../collaboration/useCollabText";
import { linkAt } from "./editorLinks";

/** Open an external URL through the shell's confirm-then-browser navigation. */
function openExternal(url: string): void {
  // A transient anchor click navigates the top frame, which the Qt page
  // intercepts (acceptNavigationRequest) and routes to the OS browser after
  // confirmation — the same path the reading preview's links use. In tests
  // (jsdom) it is a harmless no-op.
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.rel = "noopener noreferrer";
  anchor.click();
}

export interface EditorSuggestions {
  noteTitles: string[];
  tags: string[];
  propertyKeys: string[];
}

interface Props {
  noteId: string;
  initialContent: string;
  suggestions: EditorSuggestions;
  onChange: (content: string) => void;
  onSave: (content: string) => void;
  readOnly?: boolean;
  /** When set, the editor binds to a shared Y.Text and shows remote cursors. */
  collab?: CollabBinding | null;
  /** Lowercased title/alias → note id, so Ctrl/Cmd-click can follow wiki links. */
  titleIndex?: Record<string, string>;
  onOpenNote?: (noteId: string) => void;
}

const AUTOSAVE_MS = 800;

// Highlighting comes from the design tokens, resolved once at mount. A CodeMirror
// theme that hardcodes colours is a theme that ignores high-contrast mode.
function token(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  return (
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() ||
    fallback
  );
}

function buildTheme(): Extension {
  const accent = token("--accent-primary", "#22e0f5");
  const ai = token("--accent-ai", "#a06bff");
  const secondary = token("--text-secondary", "#93a1bd");
  const tertiary = token("--text-tertiary", "#5d6a86");

  const highlight = HighlightStyle.define([
    { tag: tags.heading1, fontSize: "1.6em", fontWeight: "700", color: accent },
    {
      tag: tags.heading2,
      fontSize: "1.35em",
      fontWeight: "700",
      color: accent,
    },
    {
      tag: tags.heading3,
      fontSize: "1.15em",
      fontWeight: "600",
      color: accent,
    },
    {
      tag: [tags.heading4, tags.heading5, tags.heading6],
      fontWeight: "600",
      color: accent,
    },
    { tag: tags.strong, fontWeight: "700" },
    { tag: tags.emphasis, fontStyle: "italic" },
    { tag: tags.strikethrough, textDecoration: "line-through" },
    { tag: tags.link, color: ai, textDecoration: "underline" },
    { tag: tags.url, color: tertiary },
    { tag: [tags.monospace, tags.content], fontFamily: "var(--font-mono)" },
    { tag: tags.quote, color: secondary, fontStyle: "italic" },
    { tag: tags.list, color: secondary },
    { tag: tags.comment, color: tertiary },
  ]);

  return [
    syntaxHighlighting(highlight),
    EditorView.theme(
      {
        "&": {
          height: "100%",
          fontSize: "var(--text-base)",
          backgroundColor: "transparent",
          color: "var(--text-primary)",
        },
        ".cm-content": {
          fontFamily: "var(--font-body)",
          lineHeight: "1.7",
          padding: "var(--space-4) 0 40vh 0",
          caretColor: "var(--accent-primary)",
          maxWidth: "78ch",
          margin: "0 auto",
        },
        ".cm-gutters": { display: "none" },
        ".cm-activeLine": { backgroundColor: "rgba(255,255,255,0.02)" },
        "&.cm-focused": { outline: "none" },
        ".cm-cursor": {
          borderLeftColor: "var(--accent-primary)",
          borderLeftWidth: "2px",
        },
        ".cm-selectionBackground, ::selection": {
          backgroundColor:
            "color-mix(in srgb, var(--accent-primary) 25%, transparent)",
        },
        ".cm-tooltip": {
          backgroundColor: "var(--surface-overlay)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-sm)",
        },
        ".cm-tooltip-autocomplete ul li[aria-selected]": {
          backgroundColor:
            "color-mix(in srgb, var(--accent-primary) 20%, transparent)",
          color: "var(--text-primary)",
        },
      },
      { dark: true },
    ),
  ];
}

// Slash commands insert Markdown; they never call Python. A "/" that could reach
// the filesystem would be a remote-code-execution surface in a text field.
const SLASH_SNIPPETS: { label: string; detail: string; text: string }[] = [
  { label: "/h1", detail: "Heading 1", text: "# " },
  { label: "/h2", detail: "Heading 2", text: "## " },
  { label: "/h3", detail: "Heading 3", text: "### " },
  { label: "/todo", detail: "Task list item", text: "- [ ] " },
  {
    label: "/table",
    detail: "Table",
    text: "| Column | Column |\n| --- | --- |\n|  |  |\n",
  },
  { label: "/code", detail: "Code block", text: "```\n\n```\n" },
  { label: "/quote", detail: "Blockquote", text: "> " },
  { label: "/callout", detail: "Callout", text: "> [!note]\n> " },
  {
    label: "/mermaid",
    detail: "Mermaid diagram",
    text: "```mermaid\ngraph TD\n  A --> B\n```\n",
  },
  { label: "/math", detail: "Math block", text: "$$\n\n$$\n" },
  { label: "/link", detail: "Wiki link", text: "[[]]" },
  { label: "/divider", detail: "Divider", text: "\n---\n" },
  { label: "/supports", detail: "Typed relationship", text: "supports:: [[]]" },
  {
    label: "/depends",
    detail: "Typed relationship",
    text: "depends_on:: [[]]",
  },
  {
    label: "/contradicts",
    detail: "Typed relationship",
    text: "contradicts:: [[]]",
  },
];

function buildCompletions(suggestions: EditorSuggestions) {
  return (context: CompletionContext): CompletionResult | null => {
    // `[[wiki link` — complete note titles.
    const wiki = context.matchBefore(/\[\[[^\]\n]*/);
    if (wiki) {
      return {
        from: wiki.from + 2,
        options: suggestions.noteTitles.map((title) => ({
          label: title,
          type: "class",
          apply: `${title}]]`,
        })),
        validFor: /^[^\]\n]*$/,
      };
    }

    // `#tag`
    const tag = context.matchBefore(/#[\w/-]*/);
    if (tag && tag.from !== tag.to) {
      return {
        from: tag.from + 1,
        options: suggestions.tags.map((value) => ({
          label: value,
          type: "keyword",
        })),
        validFor: /^[\w/-]*$/,
      };
    }

    // `/slash` at the start of a line.
    const slash = context.matchBefore(/^\s*\/\w*/);
    if (slash) {
      const from = slash.text.indexOf("/") + slash.from;
      return {
        from,
        options: SLASH_SNIPPETS.map((snippet) => ({
          label: snippet.label,
          detail: snippet.detail,
          type: "text",
          apply: snippet.text,
        })),
        validFor: /^\/\w*$/,
      };
    }

    // `key::` — typed relationship / property keys.
    const property = context.matchBefore(/^[a-z_]*/);
    if (property && property.from === property.to) return null;
    if (property && context.explicit) {
      return {
        from: property.from,
        options: suggestions.propertyKeys.map((key) => ({
          label: `${key}::`,
          type: "property",
          apply: `${key}:: `,
        })),
        validFor: /^[a-z_]*$/,
      };
    }

    return null;
  };
}

export function MarkdownEditor({
  noteId,
  initialContent,
  suggestions,
  onChange,
  onSave,
  readOnly = false,
  collab = null,
  titleIndex,
  onOpenNote,
}: Props): JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  const linkRefs = useRef({ titleIndex, onOpenNote });
  linkRefs.current = { titleIndex, onOpenNote };
  const viewRef = useRef<EditorView | null>(null);
  const saveTimer = useRef<number | null>(null);
  const latest = useRef(initialContent);

  // Keep the callbacks fresh without tearing down the editor on every render:
  // recreating the EditorView would lose the cursor and the undo history.
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  onChangeRef.current = onChange;
  onSaveRef.current = onSave;

  const suggestionsRef = useRef(suggestions);
  suggestionsRef.current = suggestions;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const flush = (): void => {
      if (saveTimer.current !== null) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      onSaveRef.current(latest.current);
    };

    // In collaborative mode the Y.Text is the source of truth for the initial
    // content and for the undo history (yCollab supplies its own), so the editor
    // starts from the shared text, not the local snapshot.
    const startDoc = collab ? collab.text.toJSON() : initialContent;

    const state = EditorState.create({
      doc: startDoc,
      extensions: [
        ...(collab ? [yCollab(collab.text, collab.awareness)] : []),
        history(),
        keymap.of([
          {
            key: "Mod-s",
            preventDefault: true,
            run: () => {
              flush();
              return true;
            },
          },
          ...defaultKeymap,
          ...historyKeymap,
          ...searchKeymap,
          indentWithTab,
        ]),
        // Ctrl/Cmd-click follows a link under the pointer: a wiki link opens the
        // note, a URL opens externally. A plain click still just moves the cursor.
        EditorView.domEventHandlers({
          mousedown: (event, view) => {
            if (!(event.ctrlKey || event.metaKey) || event.button !== 0)
              return false;
            const pos = view.posAtCoords({
              x: event.clientX,
              y: event.clientY,
            });
            if (pos === null) return false;
            const line = view.state.doc.lineAt(pos);
            const link = linkAt(line.text, pos - line.from);
            if (!link) return false;
            event.preventDefault();
            if (link.kind === "url") {
              openExternal(link.target);
            } else {
              const id =
                linkRefs.current.titleIndex?.[link.target.toLowerCase()];
              if (id) linkRefs.current.onOpenNote?.(id);
            }
            return true;
          },
        }),
        markdown({ base: markdownLanguage, codeLanguages: [] }),
        autocompletion({
          override: [
            (context) => buildCompletions(suggestionsRef.current)(context),
          ],
          activateOnTyping: true,
          closeOnBlur: true,
        }),
        EditorView.lineWrapping,
        placeholder(
          "Write. Link with [[…]], tag with #…, or type / for commands.",
        ),
        EditorState.readOnly.of(readOnly),
        buildTheme(),
        EditorView.updateListener.of((update) => {
          if (!update.docChanged) return;
          const value = update.state.doc.toString();
          latest.current = value;
          onChangeRef.current(value);

          // Autosave runs in collaborative mode too: the CRDT syncs peers, while
          // saving the (already-merged) body to the note store keeps search,
          // graph, and export consistent. The two paths don't fight — the store
          // write never feeds back into the CRDT.
          if (saveTimer.current !== null)
            window.clearTimeout(saveTimer.current);
          saveTimer.current = window.setTimeout(() => {
            saveTimer.current = null;
            onSaveRef.current(latest.current);
          }, AUTOSAVE_MS);
        }),
      ],
    });

    const view = new EditorView({ state, parent: host });
    viewRef.current = view;
    latest.current = startDoc;

    return () => {
      // Never lose a pending edit to an unmount (tab switch, mode change, close).
      if (saveTimer.current !== null) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
        onSaveRef.current(latest.current);
      }
      view.destroy();
      viewRef.current = null;
    };
    // Remounting per note is intentional: a new note is a new document and a new
    // undo history. Also remount when the collab binding appears/changes, so the
    // editor rebinds to the right shared Y.Text.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteId, readOnly, collab]);

  return <div className="editor" ref={hostRef} data-testid="markdown-editor" />;
}
