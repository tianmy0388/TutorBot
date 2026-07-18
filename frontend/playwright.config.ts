/**
 * Browser-level reliability acceptance.
 *
 * `@playwright/test` is a declared development dependency. Chromium can be
 * provisioned with `npm exec --workspace frontend playwright install chromium`
 * when the local Playwright browser cache is empty.
 */
import { defineConfig } from "@playwright/test";

const baseURL = process.env.TUTOR_E2E_BASE_URL ?? "http://localhost:3010";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "test-results",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  reporter: "line",
  webServer: process.env.TUTOR_E2E_BASE_URL
    ? undefined
    : {
        command: "npm run dev",
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
      },
  use: {
    baseURL,
    locale: "zh-CN",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { browserName: "chromium", viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile-chromium",
      grep: /@core/,
      use: {
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
});
