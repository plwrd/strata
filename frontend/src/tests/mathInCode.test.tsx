/** Math is rendered outside code, and left alone inside it (bug #11). */
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownPreview } from "../features/editor/MarkdownPreview";

describe("math rendering vs code", () => {
  it("renders block math but not $ inside a fenced code block", () => {
    const content = "Real math: $$a^2$$\n\n```sh\necho $VAR1 and $VAR2\n```\n";
    const { container } = render(
      <MarkdownPreview
        content={content}
        titleIndex={{}}
        onOpenNote={() => {}}
      />,
    );
    // KaTeX rendered the real math…
    expect(container.querySelector(".katex")).toBeTruthy();
    // …but the shell snippet's $VAR survived verbatim inside the code block.
    const code = container.querySelector("code");
    expect(code?.textContent).toContain("$VAR1");
    expect(code?.textContent).toContain("$VAR2");
  });
});
