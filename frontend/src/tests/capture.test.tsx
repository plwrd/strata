/**
 * Quick capture — the dialog's contract with the backend.
 *
 * What matters: text goes to `notes.capture` with its metadata, URLs go to the
 * guarded `notes.import_url` (never fetched client-side — CSP forbids it
 * anyway), errors from the guard surface verbatim, and cancel sends nothing.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CaptureDialog } from "../features/capture/CaptureDialog";
import { captured, installFakeBridge } from "./fakeBridge";

describe("CaptureDialog", () => {
  it("captures text with its reason", async () => {
    installFakeBridge();
    const onClose = vi.fn();
    render(<CaptureDialog onClose={onClose} />);

    await userEvent.type(
      screen.getByRole("textbox", { name: "Capture content" }),
      "An idea worth keeping",
    );
    await userEvent.type(
      screen.getByPlaceholderText(/relevant to the launch/),
      "launch research",
    );
    await userEvent.click(screen.getByRole("button", { name: "Capture" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const payload = captured.find((entry) => "content" in entry);
    expect(payload?.["content"]).toBe("An idea worth keeping");
    expect(payload?.["capture_reason"]).toBe("launch research");
  });

  it("sends URLs to the guarded backend import", async () => {
    installFakeBridge();
    const onClose = vi.fn();
    render(<CaptureDialog onClose={onClose} />);

    await userEvent.click(screen.getByRole("button", { name: "From URL" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Page URL" }),
      "https://example.org/article",
    );
    await userEvent.click(screen.getByRole("button", { name: "Import page" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const payload = captured.find((entry) => "url" in entry);
    expect(payload?.["url"]).toBe("https://example.org/article");
  });

  it("shows the backend's refusal instead of pretending it worked", async () => {
    installFakeBridge({
      failWith: {
        code: "permission_denied",
        message: "This address is not reachable from URL import.",
      },
    });
    const onClose = vi.fn();
    render(<CaptureDialog onClose={onClose} />);

    await userEvent.click(screen.getByRole("button", { name: "From URL" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Page URL" }),
      "http://127.0.0.1/admin",
    );
    await userEvent.click(screen.getByRole("button", { name: "Import page" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "not reachable from URL import",
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it("the capture button stays disabled with nothing to send", () => {
    installFakeBridge();
    render(<CaptureDialog onClose={() => undefined} />);

    expect(screen.getByRole("button", { name: "Capture" })).toBeDisabled();
  });

  it("cancel sends nothing", async () => {
    installFakeBridge();
    const onClose = vi.fn();
    render(<CaptureDialog onClose={onClose} />);

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).toHaveBeenCalled();
    expect(captured).toHaveLength(0);
  });
});
