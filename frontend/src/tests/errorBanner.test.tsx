import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { ErrorBanner } from "../app/ErrorBanner";
import { useStore } from "../state/store";
import { installFakeBridge } from "./fakeBridge";

describe("ErrorBanner", () => {
  beforeEach(() => {
    installFakeBridge();
    useStore.setState({ lastError: null });
  });

  it("is hidden when there is no error", () => {
    render(<ErrorBanner />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a runtime error and can be dismissed", async () => {
    useStore.setState({ lastError: "The note could not be saved." });
    render(<ErrorBanner />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The note could not be saved.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(useStore.getState().lastError).toBeNull();
  });
});
