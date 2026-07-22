/**
 * The file tree's create actions and drag-and-drop.
 *
 * The rules under test: every layer offers its own "new note" and "new folder"
 * actions (not just the first layer), folders can grow subfolders, and a drop
 * is either a note move (internal drag) or an import (files from the OS) —
 * with text becoming notes and binary becoming attachments.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { NoteMetadata } from "../bridge/types";
import { FileTree } from "../features/explorer/FileTree";
import { useStore } from "../state/store";
import { installFakeBridge, PUBLIC_LAYER } from "./fakeBridge";

const SECOND_LAYER = {
  ...PUBLIC_LAYER,
  id: "layer_b",
  display_name: "Projects",
};

function meta(id: string, title: string, layerId = "layer_a"): NoteMetadata {
  return {
    id,
    layer_id: layerId,
    parent_id: null,
    title,
    folder_path: "Security",
    aliases: [],
    tags: [],
    properties: {},
    links: [],
    created_at: "",
    updated_at: "",
    size_bytes: 0,
    word_count: 0,
  };
}

function seed(): void {
  useStore.setState({
    connection: "ready",
    layers: [PUBLIC_LAYER, SECOND_LAYER],
    tree: {
      folders: [
        {
          id: "f1",
          layer_id: "layer_a",
          name: "Security",
          path: "Security",
          parent_id: null,
        },
      ],
      notes: [meta("n1", "Encryption Architecture")],
      locked_layer_ids: [],
    },
    trash: [],
    activeNoteId: null,
  });
}

interface Call {
  method: string;
  payload: Record<string, unknown>;
}

function installRecording(): Call[] {
  const calls: Call[] = [];
  installFakeBridge({
    onRequest: (_object, method, raw) => {
      calls.push({
        method,
        payload: (JSON.parse(raw) as { payload: Record<string, unknown> })
          .payload,
      });
    },
  });
  return calls;
}

describe("FileTree create actions", () => {
  beforeEach(() => {
    installFakeBridge();
    seed();
  });

  it("offers new-note and new-folder actions on every layer, not just the first", async () => {
    const calls = installRecording();
    seed();
    const user = userEvent.setup();
    render(<FileTree />);

    await user.click(screen.getByTitle("New note in Projects"));
    await user.click(screen.getByTitle("New folder in Projects"));

    await waitFor(() => {
      const created = calls.find((call) => call.method === "create_note");
      expect(created?.payload["layer_id"]).toBe("layer_b");
      const folder = calls.find((call) => call.method === "create_folder");
      expect(folder?.payload["layer_id"]).toBe("layer_b");
    });
  });

  it("creates a subfolder inside a folder", async () => {
    const calls = installRecording();
    seed();
    const user = userEvent.setup();
    render(<FileTree />);

    await user.click(screen.getByTitle("New subfolder"));

    await waitFor(() => {
      const folder = calls.find((call) => call.method === "create_folder");
      expect(folder?.payload["folder_path"]).toBe("Security");
      expect(folder?.payload["layer_id"]).toBe("layer_a");
    });
  });
});

describe("FileTree drag and drop", () => {
  beforeEach(() => {
    installFakeBridge();
    seed();
  });

  const folderRow = (): HTMLElement =>
    screen.getByText("Security").closest("[role=treeitem]")!;

  it("moves a dragged note into the folder it is dropped on", async () => {
    const calls = installRecording();
    seed();
    render(<FileTree />);

    fireEvent.drop(folderRow(), {
      dataTransfer: {
        files: [],
        getData: (type: string) => (type === "text/strata-note" ? "n1" : ""),
      },
    });

    await waitFor(() => {
      const move = calls.find((call) => call.method === "move_note");
      expect(move?.payload).toMatchObject({
        note_id: "n1",
        folder_path: "Security",
      });
    });
  });

  it("imports a markdown file dropped from the OS as a note in that folder", async () => {
    const calls = installRecording();
    seed();
    render(<FileTree />);

    fireEvent.drop(folderRow(), {
      dataTransfer: {
        files: [new File(["# Imported\n"], "Imported.md")],
        getData: () => "",
      },
    });

    await waitFor(() => {
      const created = calls.find((call) => call.method === "create_note");
      expect(created?.payload).toMatchObject({
        layer_id: "layer_a",
        title: "Imported",
        folder_path: "Security",
        content: "# Imported\n",
      });
    });
  });

  it("imports a binary file as an attachment wrapped in a note", async () => {
    const calls = installRecording();
    seed();
    render(<FileTree />);

    fireEvent.drop(folderRow(), {
      dataTransfer: {
        files: [new File([new Uint8Array([1, 2, 3])], "diagram.png")],
        getData: () => "",
      },
    });

    await waitFor(() => {
      const attached = calls.find((call) => call.method === "save_attachment");
      expect(attached?.payload["filename"]).toBe("diagram.png");
      const created = calls.find((call) => call.method === "create_note");
      expect(created?.payload["title"]).toBe("diagram");
      expect(created?.payload["content"]).toContain("attachments/");
    });
  });

  it("accepts a drop on the layer name as a move to the layer root", async () => {
    const calls = installRecording();
    seed();
    render(<FileTree />);

    const layerRow = screen.getByText("Knowledge").closest("div")!;
    fireEvent.drop(layerRow, {
      dataTransfer: {
        files: [],
        getData: (type: string) => (type === "text/strata-note" ? "n1" : ""),
      },
    });

    await waitFor(() => {
      const move = calls.find((call) => call.method === "move_note");
      expect(move?.payload).toMatchObject({ note_id: "n1", folder_path: "" });
    });
  });
});
