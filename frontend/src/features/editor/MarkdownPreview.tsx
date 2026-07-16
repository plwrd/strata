/**
 * Reading mode.
 *
 * The HTML inserted here has been through DOMPurify (see `markdown.ts`); this
 * component adds the two renderers that need a DOM to exist first — Mermaid and
 * KaTeX — and wires wiki links to in-app navigation.
 *
 * Mermaid and KaTeX both run on *note content*, which is untrusted. Both are
 * sandboxed by rendering into an element we own, catching their failures, and
 * falling back to showing the source rather than letting an exception blank the
 * pane. Mermaid's own `securityLevel: "strict"` disables the HTML labels and
 * click handlers that would otherwise be an injection route.
 */

import katex from "katex";
import "katex/dist/katex.min.css";
import { useEffect, useMemo, useRef } from "react";
import { renderMarkdown, type RenderContext } from "./markdown";

interface Props {
  content: string;
  titleIndex: Record<string, string>;
  onOpenNote: (noteId: string) => void;
}

const MATH_BLOCK = /\$\$([\s\S]+?)\$\$/g;
const MATH_INLINE = /(?<!\$)\$([^$\n]+?)\$(?!\$)/g;

// Apply KaTeX only outside code. A shell snippet with `$VAR` is not math, and
// rendering it as KaTeX turns a code block into escaped HTML soup. We split on
// fenced blocks (```…```) and inline code spans (`…`) and leave those untouched.
function renderMathOutsideCode(source: string): string {
  const CODE = /(```[\s\S]*?```|`[^`\n]*`)/g;
  return source
    .split(CODE)
    .map((segment, i) => (i % 2 === 1 ? segment : renderMath(segment)))
    .join("");
}

function renderMath(source: string): string {
  const block = source.replace(MATH_BLOCK, (_match, expression: string) => {
    try {
      return katex.renderToString(expression.trim(), {
        displayMode: true,
        throwOnError: false,
      });
    } catch {
      return `<pre class="math-error">${expression}</pre>`;
    }
  });
  return block.replace(MATH_INLINE, (_match, expression: string) => {
    try {
      return katex.renderToString(expression.trim(), {
        displayMode: false,
        throwOnError: false,
      });
    } catch {
      return expression;
    }
  });
}

export function MarkdownPreview({
  content,
  titleIndex,
  onOpenNote,
}: Props): JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);

  const html = useMemo(() => {
    const context: RenderContext = { titleIndex };
    return renderMarkdown(renderMathOutsideCode(content), context);
  }, [content, titleIndex]);

  // Wiki links carry `data-note`, never an href, so there is no URL for a note to
  // abuse and no navigation for Qt to intercept.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const handler = (event: MouseEvent): void => {
      const target = (event.target as HTMLElement).closest<HTMLElement>(
        "[data-note]",
      );
      if (!target) return;
      event.preventDefault();
      const noteId = target.dataset["note"];
      if (noteId) onOpenNote(noteId);
    };

    host.addEventListener("click", handler);
    return () => host.removeEventListener("click", handler);
  }, [onOpenNote]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const blocks = host.querySelectorAll<HTMLElement>(
      "pre > code.language-mermaid",
    );
    if (blocks.length === 0) return;

    let cancelled = false;

    void (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "dark",
          // Untrusted input: no HTML labels, no click bindings, no script tags.
          securityLevel: "strict",
          darkMode: true,
        });

        for (const [index, block] of Array.from(blocks).entries()) {
          if (cancelled) return;
          const source = block.textContent ?? "";
          const container = block.parentElement;
          if (!container) continue;
          try {
            const { svg } = await mermaid.render(
              `mermaid-${Date.now()}-${index}`,
              source,
            );
            const figure = document.createElement("figure");
            figure.className = "mermaid";
            figure.innerHTML = svg;
            container.replaceWith(figure);
          } catch {
            // A diagram that will not parse stays visible as its source, which is
            // more useful than an empty box and cannot take the pane down.
            container.classList.add("mermaid--failed");
          }
        }
      } catch {
        // Mermaid could not load at all (blocked, offline chunk missing). The code
        // blocks simply remain code blocks.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [html]);

  return (
    <div
      ref={hostRef}
      className="preview"
      data-testid="markdown-preview"
      // Sanitised in markdown.ts. This is the only dangerouslySetInnerHTML in the
      // app, and it is the reason the sanitiser allowlist is conservative.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
