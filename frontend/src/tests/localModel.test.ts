import { describe, expect, it } from "vitest";
import { pickInstalledModel } from "../features/ai-composer/localModel";

describe("pickInstalledModel", () => {
  it("prefers Qwythos over the old Distill Qwen default", () => {
    expect(pickInstalledModel(["llama3", "qwythos"], "deepseek-r1:7b")).toBe(
      "qwythos",
    );
    expect(pickInstalledModel(["llama3", "qwythos"], "qwythos")).toBe(
      "qwythos",
    );
  });

  it("keeps an explicit non-legacy choice that is installed", () => {
    expect(pickInstalledModel(["llama3", "qwythos"], "llama3")).toBe("llama3");
  });

  it("falls back to the first listed model when Qwythos is missing", () => {
    expect(pickInstalledModel(["phi3"], "qwythos")).toBe("phi3");
  });
});
