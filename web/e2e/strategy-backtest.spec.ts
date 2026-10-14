import { test, expect } from "@playwright/test";

/**
 * Quant-strategy happy path (tasks 9.2-9.5): create a template strategy from
 * the catalog, run a backtest, and see the equity curve + headline stats.
 *
 * Runs against the simulated + SQLite backend (no real market data). The
 * template track is allowed for the default group (only custom code is gated),
 * so a freshly registered user can create and backtest it.
 */
test("create template strategy -> backtest -> view curve", async ({ page }) => {
  const email = `e2e_strat_${Date.now()}@example.com`;
  const password = "password123";

  // Register -> auto-login -> dashboard.
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole("link", { name: "Register" }).click();
  await page.locator('input[type="email"]').fill(email);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page).toHaveURL(/\/$/);

  // Open the strategies page.
  await page.getByRole("link", { name: "Strategies" }).click();
  await expect(page).toHaveURL(/\/strategies$/);

  // Create a template strategy.
  await page.getByRole("button", { name: "New strategy" }).click();
  await page.getByLabel("strategy name").fill("E2E SMA cross");
  await page.getByLabel("symbol").fill("AAPL");
  // The template select defaults to the first catalog entry (SMA crossover).
  await page.getByRole("button", { name: "Create strategy" }).click();

  // Lands on the strategy detail page.
  await expect(page).toHaveURL(/\/strategies\/[\da-f-]+$/);
  await expect(page.getByText("E2E SMA cross")).toBeVisible();

  // Run a backtest (default 60-day window + 100k cash).
  await page.getByRole("button", { name: "Run backtest" }).click();

  // Wait for the backtest to complete and render stats + the equity curve.
  await expect(page.getByText("Total return")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Equity curve")).toBeVisible();
  // The run history table picks up the COMPLETED backtest.
  await expect(page.getByRole("row", { name: /Backtest/ })).toBeVisible();
});
