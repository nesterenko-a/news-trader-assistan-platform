import { defineConfig } from "@playwright/test";

/**
 * E2E-тесты веб-интерфейса (Playwright Test Runner).
 *
 * Стенд: globalSetup поднимает uvicorn с тестовой SQLite-БД (сидинг из
 * scripts/e2e_seed.py) на 127.0.0.1:8765; globalTeardown гасит его.
 * Запуск: `npx playwright test`; UI-режим: `npx playwright test --ui`.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  // Сервер и БД общие — прогон последовательный, один worker.
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  outputDir: "test-results",
  use: {
    baseURL: "http://127.0.0.1:8765",
    headless: true,
    screenshot: "only-on-failure",
    video: "off",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
