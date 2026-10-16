import { test, expect } from "@playwright/test";
import { login, USER, ADMIN } from "./helpers";

test("исходы: личная история, фильтры, актуальность и подписка", async ({ page }) => {
  await login(page, USER.username, USER.password);
  await page.goto("/top5");
  await page.getByRole("link", { name: "Исходы", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Исходы сценариев Top-5" })).toBeVisible();
  await page.locator('input[name="ticker"]').fill("OUTCOMEE2E");
  await page.getByRole("button", { name: "Применить" }).click();
  await expect(page.locator(".outcome-row")).toHaveCount(3);
  const row = page.locator(".outcome-row").filter({ hasText: "OUTCOMEE2E · A" });
  await row.locator("summary").click();
  await expect(row).toContainText("Наблюдение задерживается");
  await expect(row).toContainText("Системный горизонт оценки — 7 календарных дней");
  await expect(row).toContainText("Событий пока нет");
  await row.getByRole("button", { name: "Уведомлять в вебе" }).click();
  await expect(row.locator(".subscription-status")).toHaveText("Сохранено");
  await expect(row.getByRole("button", { name: "Отключить уведомления" })).toBeVisible();
  await page.reload();
  await row.locator("summary").click();
  await row.getByRole("button", { name: "Отключить уведомления" }).click();
  await expect(row.locator(".subscription-status")).toHaveText("Сохранено");
  await row.locator(".scenario-telegram").check();
  await row.getByRole("button", { name: "Уведомлять в вебе" }).click();
  await expect(row.locator(".subscription-status")).toContainText("Сначала привяжите Telegram");
  await page.locator('input[name="ticker"]').fill("NO_SUCH_OUTCOME");
  await page.getByRole("button", { name: "Применить" }).click();
  await expect(page.locator("#outcomes-empty")).toBeVisible();
});

test("исходы: администратор не видит личную историю другого пользователя", async ({ page }) => {
  await login(page, ADMIN.username, ADMIN.password);
  await page.goto("/top5?tab=outcomes&ticker=OUTCOMEE2E");
  await expect(page.locator(".outcome-row")).toHaveCount(0);
  await expect(page.locator("#outcomes-empty")).toBeVisible();
});

test("исходы: мобильный экран и доступ без входа", async ({ page }) => {
  await page.goto("/top5?tab=outcomes");
  await expect(page).toHaveURL(/\/login/);
  await login(page, USER.username, USER.password);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/top5?tab=outcomes&ticker=OUTCOMEE2E");
  await expect(page.getByRole("heading", { name: "Исходы сценариев Top-5" })).toBeVisible();
  await expect(page.locator(".outcome-row")).toHaveCount(3);
});
