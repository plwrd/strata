/**
 * Markdown rendering.
 *
 * Note content is untrusted input — it can come from an import, a collaborator, or
 * an AI response. So the pipeline is: parse to HTML, then **sanitise**, and only
 * then insert. Raw HTML in a note is not rendered as HTML by default; the
 * sanitiser strips scripts, event handlers, and anything that can navigate or
 * fetch.
 *
 * Wiki links are resolved to internal buttons *before* parsing, so a note can
 * never author an `<a href="javascript:...">` by writing a link.
 */

import DOMPurify from "dompurify";
import { marked } from "marked";

// A conservative allowlist. Anything not named here is dropped, including the
// `<script>`, `<iframe>`, `<object>` and `<form>` a hostile note would want.
const ALLOWED_TAGS = [
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "p",
  "br",
  "hr",
  "blockquote",
  "pre",
  "code",
  "ul",
  "ol",
  "li",
  "input",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "strong",
  "em",
  "del",
  "sup",
  "sub",
  "mark",
  "a",
  "img",
  "span",
  "div",
  "section",
  "figure",
  "figcaption",
  "details",
  "summary",
  // KaTeX and Mermaid render into these.
  "svg",
  "path",
  "g",
  "rect",
  "circle",
  "line",
  "polygon",
  "polyline",
  "text",
  "tspan",
  "defs",
  "marker",
  "foreignObject",
  "style",
  "math",
  "semantics",
  "mrow",
  "mi",
  "mo",
  "mn",
  "msup",
  "msub",
  "mfrac",
  "annotation",
];

const ALLOWED_ATTR = [
  "href",
  "src",
  "alt",
  "title",
  "class",
  "id",
  "type",
  "checked",
  "disabled",
  "colspan",
  "rowspan",
  "align",
  "open",
  "viewBox",
  "d",
  "fill",
  "stroke",
  "stroke-width",
  "x",
  "y",
  "x1",
  "y1",
  "x2",
  "y2",
  "cx",
  "cy",
  "r",
  "rx",
  "ry",
  "width",
  "height",
  "points",
  "transform",
  "marker-end",
  "marker-start",
  "text-anchor",
  "dominant-baseline",
  "style",
  "data-note",
  "data-broken",
  "aria-hidden",
];

marked.setOptions({ gfm: true, breaks: false });

const WIKI_LINK = /\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|([^\]]+))?\]\]/g;
const TYPED_PREFIX = /^([a-z_]+)::\s*$/;

export interface RenderContext {
  /** Lowercased title/alias → note id. Used to mark broken links. */
  titleIndex: Record<string, string>;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Replace wiki links with internal anchors before Markdown parsing.
 *
 * These become `<a data-note="...">` with no `href`, so there is no URL to
 * navigate to and no scheme for a note to abuse. The click handler in the
 * preview component turns `data-note` into an in-app navigation.
 */
export function replaceWikiLinks(
  source: string,
  context: RenderContext,
): string {
  return source.replace(
    WIKI_LINK,
    (_match, rawTarget: string, alias?: string) => {
      const target = rawTarget.trim();
      const noteId = context.titleIndex[target.toLowerCase()];
      const label = escapeHtml((alias ?? target).trim());
      if (!noteId) {
        return `<a class="wikilink wikilink--broken" data-broken="true" title="This note does not exist yet">${label}</a>`;
      }
      return `<a class="wikilink" data-note="${escapeHtml(noteId)}">${label}</a>`;
    },
  );
}

/** Strip the `relationship::` marker lines from the rendered view. */
function renderTypedMarkers(source: string): string {
  return source
    .split("\n")
    .map((line) => {
      const [prefix, ...rest] = line.split(/(?<=::)\s/);
      const match = TYPED_PREFIX.exec(prefix ?? "");
      if (match && rest.length > 0) {
        return `<span class="relationship">${escapeHtml(match[1]!.replace(/_/g, " "))}</span> ${rest.join(" ")}`;
      }
      return line;
    })
    .join("\n");
}

export function renderMarkdown(source: string, context: RenderContext): string {
  const withLinks = replaceWikiLinks(renderTypedMarkers(source), context);
  const html = marked.parse(withLinks, { async: false });

  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    // Belt and braces: even if an `href` survives, only these schemes may appear.
    ALLOWED_URI_REGEXP:
      /^(?:https?:|mailto:|#|\/|attachments\/|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
    FORBID_TAGS: [
      "script",
      "iframe",
      "object",
      "embed",
      "form",
      "input[type=file]",
    ],
    FORBID_ATTR: ["onerror", "onload", "onclick", "formaction", "srcdoc"],
  });
}

/** Headings, for the outline and for heading autocomplete. */
export function extractHeadings(
  source: string,
): { level: number; text: string }[] {
  const headings: { level: number; text: string }[] = [];
  for (const line of source.split("\n")) {
    const match = /^(#{1,6})\s+(.*)$/.exec(line);
    if (match) {
      headings.push({ level: match[1]!.length, text: match[2]!.trim() });
    }
  }
  return headings;
}
