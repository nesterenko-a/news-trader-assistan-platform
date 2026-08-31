import { test, expect } from "@playwright/test";
import { ADMIN, USER, login } from "./helpers";

test.describe("Контроль доступа", () => {
  const PROTECTED = [
    "/watchlist",
    "/portfolio",
    "/alerts",
    "/history",
    "/paper",
    "/news",
    "/admin",
  ];
  for (const path of PROTECTED) {
    test(`аноним: редирект на /login (${path})`, async ({ page }) => {
      await page.goto(path);
      expect(page.url()).toContain("/login");
    });
  }

  test("обычный пользователь на /admin получает 403", async ({ page }) => {
    await login(page, USER.username, USER.password);
    const response = await page.goto("/admin");
    expect(response?.status()).toBe(403);
    expect(page.url()).toContain("/admin");
  });

  test("admin: доступ к /admin", async ({ page }) => {
    await login(page, ADMIN.username, ADMIN.password);
    await page.goto("/admin");
    await expect(page.locator("body")).toContainText("Администрирование");
    await expect(page.locator('form[action="/admin/scripts/run"]')).not.toHaveCount(0);
    await expect(
      page.locator('form:has(input[value="update_oi"]) button[type="submit"]'),
    ).toBeEnabled();
    await expect(
      page.locator('form:has(input[value="realtime_updater"]) button[type="submit"]'),
    ).toBeEnabled();
  });

  test("admin: перенос графа — скачивание дампа и форма импорта", async ({ page }) => {
    await login(page, ADMIN.username, ADMIN.password);
    await page.goto("/admin");
    await expect(page.locator("body")).toContainText("Перенос графа");
    const dl = page.locator('a[href="/admin/graph/export"]');
    await expect(dl).toHaveCount(1);
    const downloadPromise = page.waitForEvent("download");
    await dl.click();
    const download = await downloadPromise;
    // имя файла вида {ГГГГММДД_ЧЧММСС}_seed_dump.jsonl
    expect(download.suggestedFilename()).toMatch(/^\d{8}_\d{6}_seed_dump\.jsonl$/);
    // форма импорта (выбор файла через проводник)
    const importForm = page.locator('form[action="/admin/graph/import"]');
    await expect(importForm).toHaveCount(1);
    await expect(importForm.locator('input[type="file"][name="file"]'));
  });

  test("авторизованный пользователь может снять одно предупреждение и все предупреждения", async ({ page }) => {
    await login(page, USER.username, USER.password);
    let notices = [
      { id: 101, level: "warning", source: "rss", text: "Лента временно недоступна", created_at: "31.08.2026 12:00" },
      { id: 102, level: "info", source: "stale_prices", text: "Цены давно не обновлялись", created_at: "31.08.2026 11:00" },
    ];
    await page.route("**/api/notices", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ contentType: "application/json", body: JSON.stringify({ state: "warning", notices, can_dismiss: true }) });
        return;
      }
      await route.continue();
    });
    await page.route("**/api/notices/*/dismiss", async (route) => {
      const id = Number(route.request().url().match(/notices\/(\d+)\/dismiss/)?.[1]);
      notices = notices.filter((notice) => notice.id !== id);
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ dismissed: 1 }) });
    });
    await page.route("**/api/notices/dismiss-all", async (route) => {
      notices = [];
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ dismissed: 1 }) });
    });
    await page.goto("/");

    await page.locator(".attention-toggle").click();
    await expect(page.getByText("Лента временно недоступна")).toBeVisible();
    await page.getByRole("button", { name: "Отметить прочитанным" }).first().click();
    await expect(page.getByText("Лента временно недоступна")).toHaveCount(0);
    await page.getByRole("button", { name: "Прочитать всё" }).click();
    await expect(page.locator("#attention-list")).toContainText("Ошибок нет");
  });
});
