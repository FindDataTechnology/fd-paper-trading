import { test, expect } from "@playwright/test";

/**
 * Full mobile trading journey on /m in a real browser (phone viewport)
 * against the FastAPI backend (simulated market data, SQLite). Covers:
 * register -> open /m -> create account -> Trade tab market buy -> Positions
 * tab sees the position -> Trade tab resting limit order -> Orders tab cancel.
 */
test("mobile /m trading journey", async ({ page }) => {
  // iPhone-ish viewport to exercise the mobile (bottom tab bar) layout.
  await page.setViewportSize({ width: 390, height: 844 });
  // Auto-accept window.confirm() in-page so cancel/close actions never block on
  // a native dialog. Playwright auto-dismisses unhandled dialogs, which races
  // with the click and can leave the DELETE never sent; stubbing confirm avoids
  // the dialog entirely.
  await page.addInitScript(() => {
    (globalThis as any).confirm = () => true;
  });

  const email = `m_e2e_${Date.now()}@example.com`;
  const password = "password123";

  // 1. Register (auto-logs in, lands on the desktop dashboard).
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole("link", { name: "Register" }).click();
  await page.locator('input[type="email"]').fill(email);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page).toHaveURL(/\/$/);

  // 2. Open the mobile surface.
  await page.goto("/m");
  await expect(page.getByText("fd-paper-trading")).toBeVisible();
  for (const label of ["Watch", "Trade", "Pos", "Orders", "Strat"]) {
    await expect(page.getByRole("button", { name: label })).toBeVisible();
  }

  // 3. Create a paper account (a new user has none); the switcher auto-selects it.
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("combobox")).toBeVisible({ timeout: 10_000 });

  // 4. Trade tab: MARKET BUY 10 AAPL -> fills immediately.
  await page.getByRole("button", { name: "Trade" }).click();
  await page.locator('input[inputmode="decimal"]').first().fill("10");
  await page.getByRole("button", { name: /BUY AAPL/ }).click();
  await expect(page.getByText("Order submitted")).toBeVisible({ timeout: 10_000 });

  // 5. Positions tab: shows the resulting AAPL position.
  await page.getByRole("button", { name: "Pos" }).click();
  await expect(page.getByText("AAPL").first()).toBeVisible({ timeout: 10_000 });

  // 6. Trade tab again: place a resting LIMIT BUY (stays OPEN, cancellable).
  await page.getByRole("button", { name: "Trade" }).click();
  await page.getByLabel("Type").selectOption("LIMIT");
  await page.locator('input[inputmode="decimal"]').first().fill("5");
  await page.locator('input[inputmode="decimal"]').last().fill("1.00");
  await page.getByRole("button", { name: /BUY AAPL/ }).click();
  await expect(page.getByText("Order submitted")).toBeVisible({ timeout: 10_000 });

  // 7. Orders tab: cancel the open limit order.
  await page.getByRole("button", { name: "Orders" }).click();
  // exact:true disambiguates the Cancel action from the "Cancelled" filter chip
  // (Playwright name matching is substring by default).
  const cancelButton = page.getByRole("button", { name: "Cancel", exact: true });
  await expect(cancelButton).toBeVisible({ timeout: 10_000 });
  await cancelButton.click();
  // Filter to Open -> the cancelled order is excluded; the list is empty.
  // (Mirrors the desktop happy-path: rely on the server refetch with status=OPEN
  // rather than asserting the Cancel button unmounts.)
  await page.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page.getByText(/No orders for this account/)).toBeVisible({ timeout: 10_000 });

  // 8. Desktop link returns to the desktop dashboard.
  await page.getByRole("link", { name: "Desktop" }).click();
  await expect(page).toHaveURL(/\/$/);
});
