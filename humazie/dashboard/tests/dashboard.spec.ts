import { test, expect } from "@playwright/test";

test("dashboard home loads", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Humazie Bot")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review runs" })).toBeVisible();
});

test("new review page exposes controls", async ({ page }) => {
  await page.goto("/review/new");
  await expect(page.getByRole("heading", { name: "New product review" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start review" })).toBeVisible();
});
