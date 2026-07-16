/**
 * Link detection under the cursor in the editor's source view.
 */

import { describe, expect, it } from "vitest";
import { linkAt } from "../features/editor/editorLinks";

describe("linkAt", () => {
  it("finds a wiki link when the column is inside it", () => {
    const line = "See [[Threat Model]] here.";
    expect(linkAt(line, 8)).toEqual({ kind: "note", target: "Threat Model" });
  });

  it("returns the target, not the alias, for a piped wiki link", () => {
    const line = "See [[threat-model|the model]] now.";
    expect(linkAt(line, 10)?.target).toBe("threat-model");
  });

  it("strips a heading anchor from the wiki target", () => {
    expect(linkAt("[[Note#Section]]", 3)).toEqual({
      kind: "note",
      target: "Note",
    });
  });

  it("finds a bare URL", () => {
    const line = "docs at https://example.com/path, see it.";
    const link = linkAt(line, 15);
    expect(link).toEqual({
      kind: "url",
      target: "https://example.com/path",
    });
  });

  it("does not swallow trailing sentence punctuation into the URL", () => {
    // The comma after the URL must not be part of the target.
    const line = "go to https://example.com.";
    expect(linkAt(line, 10)?.target).toBe("https://example.com");
  });

  it("returns null off any link", () => {
    expect(linkAt("just some plain prose", 4)).toBeNull();
    expect(linkAt("See [[Threat Model]] here.", 24)).toBeNull();
  });

  it("prefers a wiki link over a URL at the same spot", () => {
    const line = "[[https://not-really-a-url]]";
    expect(linkAt(line, 5)?.kind).toBe("note");
  });
});
