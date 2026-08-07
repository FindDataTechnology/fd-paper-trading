import { test, expect } from "@playwright/test";

/**
 * Verifies the chat gating + styling change (fix-chat-ui-issues tasks 2, 4, 5):
 *   - /login and /register have NO CopilotKit chat DOM (ChatProvider is outside
 *     the unauthenticated routes)                              -> task 4
 *   - a protected page (dashboard) DOES render the CopilotKit chat -> task 5
 *   - opening the chat via the launcher shows the styled sidebar
 *     with the "Trading Assistant" header                       -> task 2
 *   - after logout, the chat disappears (back on /login)        -> task 13
 *
 * Runs against the simulated backend (no CopilotKit runtime on :4100). The
 * CopilotSidebar UI shell renders independently of runtime connectivity.
 */

const chatDom = (page: import("@playwright/test").Page) =>
  page.locator('[class*="copilotKit"], [class*="copilotkit-"]');

test("chat is absent on /login, present and openable on protected pages", async ({ page }) => {
  // Task 4: /login must not render the chat.
  await page.goto("/login");
  await expect(page).toHaveURL(/\/login$/);
  await expect(chatDom(page)).toHaveCount(0);

  // Task 4 (cont.): /register must not render the chat either.
  await page.getByRole("link", { name: "Register" }).click();
  await expect(page).toHaveURL(/\/register$/);
  await expect(chatDom(page)).toHaveCount(0);

  // Register -> auto-lands on the dashboard (a protected page).
  await page.locator('input[type="email"]').fill(`chat_${Date.now()}@example.com`);
  await page.locator('input[type="password"]').fill("password123");
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page).toHaveURL(/\/$/);

  // Task 5: a protected page (dashboard) must render the chat DOM.
  await expect(chatDom(page).first()).toBeAttached({ timeout: 10_000 });
  expect(await chatDom(page).count()).toBeGreaterThan(0);

  // Task 2: open the chat via its launcher and confirm the styled sidebar
  // renders with the "Trading Assistant" header (label from ChatProvider).
  const openButton = page.getByRole("button", { name: "Open Chat" });
  if (await openButton.count()) {
    await openButton.click();
  }
  await expect(page.getByText("Trading Assistant")).toBeVisible({ timeout: 10_000 });

  // Task 13: log out -> redirected to /login, and the chat disappears.
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(chatDom(page)).toHaveCount(0);
});
