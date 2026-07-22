/**
 * Turning files dropped from the operating system into importable content.
 *
 * Classification and title derivation are pure so they can be unit tested; only
 * `readDroppedFiles` touches the File API. Markdown and plain text become note
 * bodies verbatim; anything else is carried as base64 so Python can store it as
 * an attachment (and, for a private layer, encrypt it) — the renderer never
 * decides where bytes land on disk.
 */

export interface ImportedFile {
  /** Original filename, extension included — what the attachment is saved as. */
  name: string;
  /** Filename without its extension — the imported note's title. */
  title: string;
  kind: "text" | "binary";
  /** The note body, verbatim, for text files; empty for binary ones. */
  text: string;
  /** The raw bytes, base64-encoded, for binary files; empty for text ones. */
  base64: string;
}

const TEXT_EXTENSIONS = new Set(["md", "markdown", "txt"]);

export function isTextFile(filename: string): boolean {
  const dot = filename.lastIndexOf(".");
  if (dot <= 0) return false;
  return TEXT_EXTENSIONS.has(filename.slice(dot + 1).toLowerCase());
}

/** "Meeting notes.md" → "Meeting notes"; a dotfile keeps its full name. */
export function titleOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  const stem = dot > 0 ? filename.slice(0, dot) : filename;
  return stem.trim() || filename;
}

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1)
    binary += String.fromCharCode(bytes[i]!);
  return btoa(binary);
}

/** Read every dropped file, in order, into an importable shape. */
export async function readDroppedFiles(files: File[]): Promise<ImportedFile[]> {
  const imported: ImportedFile[] = [];
  for (const file of files) {
    if (isTextFile(file.name)) {
      imported.push({
        name: file.name,
        title: titleOf(file.name),
        kind: "text",
        text: await file.text(),
        base64: "",
      });
    } else {
      imported.push({
        name: file.name,
        title: titleOf(file.name),
        kind: "binary",
        text: "",
        base64: toBase64(await file.arrayBuffer()),
      });
    }
  }
  return imported;
}
