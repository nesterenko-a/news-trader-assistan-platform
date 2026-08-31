import { test, expect } from "@playwright/test";
import { register } from "./helpers";

async function newUser(page: import("@playwright/test").Page): Promise<string> {
  const name = "u_" + crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  await register(page, name, "secret123");
  return name;
}

test.describe("Приватные функции", () => {
  test("watchlist: добавить и удалить", async ({ page }) => {
    await newUser(page);
    await page.goto("/watchlist");
    await expect(page.locator("body")).toContainText("Список пуст");

    await page.fill('form[action="/api-watchlist-add"] input[name="ticker"]', "AFLT");
    await Promise.all([
      page.waitForURL("/watchlist"),
      page.click('form[action="/api-watchlist-add"] button[type="submit"]'),
    ]);
    await expect(page.locator("body")).toContainText("AFLT");
    await expect(page.locator("body")).not.toContainText("Список пуст");

    await Promise.all([
      page.waitForURL("/watchlist"),
      page.click('form[action="/api-watchlist-remove"] button[type="submit"]'),
    ]);
    await expect(page.locator("body")).toContainText("Список пуст");
  });

  test("портфель: добавить и закрыть позицию", async ({ page }) => {
    await newUser(page);
    await page.goto("/portfolio");
    await expect(page.locator("body")).toContainText("Позиций нет");

    const add = page.locator('form[action="/api-portfolio-add"]');
    await add.locator('input[name="ticker"]').fill("SBER");
    await add.locator('input[name="quantity"]').fill("10");
    await add.locator('input[name="avg_price"]').fill("100");
    await Promise.all([
      page.waitForURL("/portfolio"),
      add.locator('button[type="submit"]').click(),
    ]);
    await expect(page.locator("body")).toContainText("SBER");

    // закрытие через модалку
    await page.locator('button[onclick^="openCloseModal"]').click();
    await Promise.all([
      page.waitForURL("/portfolio"),
      page.locator('#close-modal button[name="rating"][value="neutral"]').click(),
    ]);
    await expect(page.locator("body")).not.toContainText("SBER");
  });

  test("алерты: сохранение настроек", async ({ page }) => {
    await newUser(page);
    await page.goto("/alerts");
    const form = page.locator('form[action="/api-alerts-settings"]');
    await form.locator('input[name="min_impact"]').fill("0.6");
    await Promise.all([
      page.waitForURL("/alerts"),
      form.locator('button[type="submit"]').click(),
    ]);
    await expect(
      page.locator('form[action="/api-alerts-settings"] input[name="min_impact"]')
    ).toHaveValue("0.6");
  });

  test("история: обратная связь", async ({ page }) => {
    await newUser(page);
    await page.goto("/history");
    await expect(page.locator("body")).toContainText("SBER");

    await Promise.all([
      page.waitForURL("/history"),
      page.locator('button[name="rating"][value="worked"]').first().click(),
    ]);
    await expect(
      page.locator('button[name="rating"][value="worked"].active')
    ).toHaveCount(1);
  });

  test("paper: страница и сброс", async ({ page }) => {
    await newUser(page);
    await page.goto("/paper");
    await expect(page.locator("body")).toContainText("Виртуальный портфель");
    await Promise.all([
      page.waitForURL("/paper"),
      page.click('form[action="/api-paper-reset"] button[type="submit"]'),
    ]);
    expect(page.url()).toContain("/paper");
  });

  test("настройки: профиль, инвестиционные ссылки и заглушки", async ({ page }) => {
    await newUser(page);
    await page.goto("/");
    await page.locator(".menu-toggle").click();
    await Promise.all([
      page.waitForURL("/settings"),
      page.locator('#user-menu a[href="/settings"]').first().click(),
    ]);
    const form = page.locator('form[action="/settings/profile"]');
    await form.locator('input[name="full_name"]').fill("Иванов Иван Иванович");
    await form.locator('input[name="display_name"]').fill("Иван");
    await Promise.all([
      page.waitForURL(/\/settings\?profile=updated/),
      form.locator('button[type="submit"]').click(),
    ]);
    await expect(page.getByText("Профиль сохранён.")).toBeVisible();
    await expect(form.locator('input[name="full_name"]')).toHaveValue("Иванов Иван Иванович");
    await expect(page.locator('a[href="/watchlist"]')).toContainText("Watchlist");
    await expect(page.getByRole("heading", { name: "Брокеры и торговля" })).toBeVisible();
    await expect(page.getByText("API-ключи пока не вводятся и не хранятся.")).toBeVisible();
  });
});
