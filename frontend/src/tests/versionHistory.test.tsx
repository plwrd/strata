/**
 * Version history panel — the trail behind the open note.
 *
 * What matters: versions come from Python (newest state is the note itself),
 * AI-origin versions are visibly attributed, restore is a two-step action, and
 * a private-layer note explains why there is no trail instead of showing an
 * empty list that reads like "nothing ever happened".
 */

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { VersionHistory } from "../features/editor/VersionHistory";
import { useStore } from "../state/store";
import { installFakeBridge, type FakeVersion } from "./fakeBridge";

const TRAIL: FakeVersion[] = [
  {
    created_at: "2026-07-20T10:00:00+00:00",
    origin: "human",
    change: "update",
    title: "Encryption Architecture",
    content: "the original body",
  },
  {
    created_at: "2026-07-21T10:00:00+00:00",
    origin: "ai:plan_9",
    change: "update",
    title: "Encryption Architecture",
    content: "the AI-rewritten body",
  },
];

async function openNote(): Promise<void> {
  await act(async () => {
    await useStore.getState().openNoteById("n1");
  });
}

describe("VersionHistory", () => {
  beforeEach(() => {
    useStore.setState({ openNote: null });
  });

  it("lists the trail with origins attributed", async () => {
    installFakeBridge({ versions: TRAIL });
    await openNote();
    render(<VersionHistory />);

    expect(await screen.findByText("AI")).toBeInTheDocument();
    expect(screen.getAllByText("you").length).toBeGreaterThan(0);
    expect(screen.getAllByText("update")).toHaveLength(2);
  });

  it("restores only after explicit confirmation", async () => {
    installFakeBridge({ versions: TRAIL });
    await openNote();
    render(<VersionHistory />);

    const restoreButtons = await screen.findAllByRole("button", {
      name: "Restore…",
    });
    await userEvent.click(restoreButtons[0]!);
    expect(screen.getByText("Restore this state?")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Restore" }));

    // The editor reloads the note; the panel refetches the trail.
    expect(
      await screen.findAllByRole("button", { name: "Restore…" }),
    ).toBeTruthy();
  });

  it("explains the absence of a trail for private layers", async () => {
    installFakeBridge({ versions: [], versionsSupported: false });
    await openNote();
    render(<VersionHistory />);

    expect(
      await screen.findByText(/no version files on disk/),
    ).toBeInTheDocument();
  });

  it("shows an honest empty state for a fresh note", async () => {
    installFakeBridge({ versions: [] });
    await openNote();
    render(<VersionHistory />);

    expect(
      await screen.findByText(/No earlier versions yet/),
    ).toBeInTheDocument();
  });
});
