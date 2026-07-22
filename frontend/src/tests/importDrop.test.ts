/**
 * Importing files dropped from the operating system.
 *
 * Classification decides where bytes go — a Markdown file becomes a note body,
 * anything else becomes an attachment — so it must be exact: a `.md` routed to
 * base64 would silently turn readable notes into opaque blobs.
 */

import { describe, expect, it } from "vitest";
import {
  isTextFile,
  readDroppedFiles,
  titleOf,
} from "../features/explorer/importDrop";

describe("isTextFile", () => {
  it("accepts markdown and plain text, case-insensitively", () => {
    expect(isTextFile("notes.md")).toBe(true);
    expect(isTextFile("notes.MD")).toBe(true);
    expect(isTextFile("notes.markdown")).toBe(true);
    expect(isTextFile("todo.txt")).toBe(true);
  });

  it("treats everything else as binary", () => {
    expect(isTextFile("scan.pdf")).toBe(false);
    expect(isTextFile("photo.png")).toBe(false);
    expect(isTextFile("archive.tar.gz")).toBe(false);
    expect(isTextFile("no-extension")).toBe(false);
    expect(isTextFile(".md")).toBe(false); // a dotfile, not an extension
  });
});

describe("titleOf", () => {
  it("strips the extension and keeps the rest verbatim", () => {
    expect(titleOf("Meeting notes.md")).toBe("Meeting notes");
    expect(titleOf("archive.tar.gz")).toBe("archive.tar");
  });

  it("keeps a dotfile or extensionless name whole", () => {
    expect(titleOf(".gitignore")).toBe(".gitignore");
    expect(titleOf("README")).toBe("README");
  });
});

describe("readDroppedFiles", () => {
  it("reads a markdown file as a note body", async () => {
    const file = new File(["# Hello\n\nBody.\n"], "Hello.md", {
      type: "text/markdown",
    });
    const [imported] = await readDroppedFiles([file]);

    expect(imported).toMatchObject({
      name: "Hello.md",
      title: "Hello",
      kind: "text",
      base64: "",
    });
    expect(imported!.text).toContain("# Hello");
  });

  it("reads a binary file as base64, byte for byte", async () => {
    const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x00, 0xff]);
    const file = new File([bytes], "pixel.png", { type: "image/png" });
    const [imported] = await readDroppedFiles([file]);

    expect(imported).toMatchObject({
      name: "pixel.png",
      title: "pixel",
      kind: "binary",
      text: "",
    });
    const decoded = atob(imported!.base64);
    expect(decoded.length).toBe(bytes.length);
    expect([...decoded].map((c) => c.charCodeAt(0))).toEqual([...bytes]);
  });

  it("preserves the drop order", async () => {
    const files = [
      new File(["a"], "a.md"),
      new File(["b"], "b.txt"),
      new File([new Uint8Array([1])], "c.bin"),
    ];
    const imported = await readDroppedFiles(files);
    expect(imported.map((f) => f.title)).toEqual(["a", "b", "c"]);
  });
});
