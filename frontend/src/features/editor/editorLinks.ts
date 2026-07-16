/**
 * Finding a clickable link under the cursor in the *editor* (source) view.
 *
 * The reading preview turns `[[wiki links]]` and URLs into real anchors; the
 * CodeMirror source view did not, so a click there did nothing. This is the
 * detection half — pure and testable — used by the mod-click handler in
 * MarkdownEditor: hold Ctrl/Cmd and click a link to follow it, while a plain
 * click still just places the cursor.
 */

export interface EditorLink {
  kind: "note" | "url";
  /** The wiki target (a title/alias) or the full URL. */
  target: string;
}

// A bare URL in Markdown source. Stops before trailing punctuation so a link at
// the end of a sentence doesn't swallow the period.
const URL = /\bhttps?:\/\/[^\s<>()[\]{}"']+[^\s<>()[\]{}"'.,;:!?]/g;
// [[Target]] or [[Target|alias]] or [[Target#heading]].
const WIKI = /\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]+)?\]\]/g;

/**
 * The link occupying column `col` (0-indexed) of `line`, or null. Wiki links win
 * over URLs when both somehow overlap (a wiki link is unambiguously internal).
 */
export function linkAt(line: string, col: number): EditorLink | null {
  WIKI.lastIndex = 0;
  for (let m = WIKI.exec(line); m !== null; m = WIKI.exec(line)) {
    const start = m.index;
    const end = start + m[0].length;
    if (col >= start && col <= end) {
      return { kind: "note", target: m[1]!.trim() };
    }
  }

  URL.lastIndex = 0;
  for (let m = URL.exec(line); m !== null; m = URL.exec(line)) {
    const start = m.index;
    const end = start + m[0].length;
    if (col >= start && col <= end) {
      return { kind: "url", target: m[0] };
    }
  }

  return null;
}
