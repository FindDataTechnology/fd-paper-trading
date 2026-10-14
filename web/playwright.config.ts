import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the fd-paper-trading frontend e2e suite.
 *
 * The webServer builds the SPA (if needed) and starts the FastAPI backend
 * against a file-based SQLite DB with the simulated market-data provider, so
 * the suite needs no Postgres or broker. The backend serves the built SPA at /
 * and the API at /api/v1 and /ws on a single origin.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: "list",
  timeout: 60_000,
  use: {
    baseURL: "http://localhost:8001",
    headless: true,
    actionTimeout: 10_000,
  },
  webServer: {
    command: "bash web/e2e/run-backend.sh",
    cwd: "..",
    url: "http://localhost:8001/health",
    // Always start our own simulated+SQLite backend on the dedicated e2e port
    // (8001) so the suite never reuses a developer's :8000 server.
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
