/**
 * Markdown rendering — including the parts that stop a note from becoming code.
 *
 * Note content is untrusted: it can arrive from an import, a collaborator, or an
 * AI response. These tests are the guard on the one `dangerouslySetInnerHTML` in
 * the application.
 */

import { describe, expect, it } from "vitest";
import {
  extractHeadings,
  renderMarkdown,
  replaceWikiLinks,
} from "../features/editor/markdown";

const context = { titleIndex: { "threat model": "n2" } };

describe("renderMarkdown", () => {
  it("renders ordinary Markdown", () => {
    const html = renderMarkdown("# Title\n\nSome **bold** text.\n", context);

    expect(html).toContain("<h1>Title</h1>");
    expect(html).toContain("<strong>bold</strong>");
  });

  it("renders tables, task lists and code blocks", () => {
    const html = renderMarkdown(
      "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n- [ ] todo\n\n```js\nconst x = 1;\n```\n",
      context,
    );

    expect(html).toContain("<table>");
    expect(html).toContain('type="checkbox"');
    expect(html).toContain("<code");
  });

  it("strips a script tag", () => {
    const html = renderMarkdown(
      'Hello <script>alert("pwned")</script> world',
      context,
    );

    expect(html).not.toContain("<script");
    expect(html).not.toContain("alert(");
  });

  it("strips an inline event handler", () => {
    const html = renderMarkdown('<img src="x" onerror="alert(1)">', context);

    expect(html).not.toContain("onerror");
    expect(html).not.toContain("alert(1)");
  });

  it("strips an iframe and an object", () => {
    const html = renderMarkdown(
      '<iframe src="https://evil.example"></iframe><object data="x"></object>',
      context,
    );

    expect(html).not.toContain("<iframe");
    expect(html).not.toContain("<object");
  });

  it("neutralises a javascript: link", () => {
    const html = renderMarkdown("[click me](javascript:alert(1))", context);

    expect(html).not.toContain("javascript:alert");
  });

  it("does not let a note author a form", () => {
    const html = renderMarkdown(
      '<form action="https://evil.example"><input name="password"></form>',
      context,
    );

    expect(html).not.toContain("<form");
  });
});

describe("wiki links", () => {
  it("resolves a known target to an internal reference with no href", () => {
    const html = replaceWikiLinks("See [[Threat Model]].", context);

    expect(html).toContain('data-note="n2"');
    // No href at all: there is no URL for a note to abuse and nothing for Qt to
    // navigate to.
    expect(html).not.toContain("href=");
  });

  it("marks an unresolved target as broken rather than pretending it works", () => {
    const html = replaceWikiLinks("See [[Nowhere]].", context);

    expect(html).toContain("wikilink--broken");
    expect(html).toContain('data-broken="true"');
  });

  it("uses the alias as the label", () => {
    const html = replaceWikiLinks("See [[Threat Model|the threats]].", context);

    expect(html).toContain(">the threats<");
  });

  it("ignores a heading anchor when resolving", () => {
    const html = replaceWikiLinks("See [[Threat Model#Assets]].", context);

    expect(html).toContain('data-note="n2"');
  });

  it("escapes a hostile label", () => {
    const html = replaceWikiLinks(
      "[[Threat Model|<img src=x onerror=alert(1)>]]",
      context,
    );

    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("survives the full render pipeline", () => {
    const html = renderMarkdown("Body with [[Threat Model]] inside.", context);

    expect(html).toContain('data-note="n2"');
    expect(html).toContain("wikilink");
  });
});

describe("extractHeadings", () => {
  it("finds headings and their levels", () => {
    expect(extractHeadings("# One\n\ntext\n\n### Three\n")).toEqual([
      { level: 1, text: "One" },
      { level: 3, text: "Three" },
    ]);
  });

  it("ignores a hash that is not a heading", () => {
    expect(extractHeadings("a #tag here")).toEqual([]);
  });
});
