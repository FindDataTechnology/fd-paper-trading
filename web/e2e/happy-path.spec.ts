import { test, expect } from "@playwright/test";

/**
 * Full happy-path trading journey in a real browser against the FastAPI
 * backend (simulated market data, SQLite). Covers: register -> auto-login ->
 * create account -> search symbol -> place a market order (see the fill +
 * position) -> place a resting limit order -> cancel it (leaves the open list).
 */
test("happy-path trading journey", async ({ page }) => {
  const email = `e2e_${Date.now()}@example.com`;
  const password = "password123";

  // 1. Land on the app -> redirected to login.
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);

  // 2. Register (auto-logs in and lands on the dashboard).
  await page.getByRole("link", { name: "Register" }).click();
  await expect(page).toHaveURL(/\/register$/);
  await page.locator('input[type="email"]').fill(email);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page).toHaveURL(/\/$/);

  // 3. Create a paper account (a new user has none).
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("combobox").first()).toBeVisible();

  // 4. Search for AAPL and open the instrument page.
  await page.getByPlaceholder("Search symbol…").fill("AAPL");
  await page.getByText("Apple Inc.").click();
  await expect(page).toHaveURL(/\/instruments\/AAPL$/);

  // 5. Place a MARKET BUY of 10 -> fills immediately.
  await page.locator('input[inputmode="decimal"]').fill("10");
  await page.getByRole("button", { name: /BUY AAPL/ }).click();
  await expect(page.getByText("Order submitted")).toBeVisible();

  // 6. The dashboard shows the resulting AAPL position.
  await page.getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByText("AAPL").first()).toBeVisible();

  // 7. Place a resting LIMIT BUY (stays OPEN, cancellable).
  await page.goto("/instruments/AAPL");
  await page.getByLabel("Type").selectOption("LIMIT");
  await page.locator('input[inputmode="decimal"]').first().fill("5");
  await page.getByLabel("Limit price").fill("1.00");
  await page.getByRole("button", { name: /BUY AAPL/ }).click();
  await expect(page.getByText("Order submitted")).toBeVisible();

  // 8. On the orders page, cancel the open order and confirm it leaves the Open list.
  await page.getByRole("link", { name: "Orders" }).click();
  const cancelButton = page.getByRole("button", { name: "Cancel" });
  await expect(cancelButton).toBeVisible();
  page.on("dialog", (d) => d.accept());
  await cancelButton.click();
  await page.getByLabel("Status").selectOption("Open");
  await expect(page.getByText(/No orders for this account/)).toBeVisible();
});
