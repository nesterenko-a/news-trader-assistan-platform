import { test, expect } from "@playwright/test";
import { USER, login } from "./helpers";

async function menuOpen(page: import("@playwright/test").Page): Promise<boolean> {
  const cls = await page.locator("#user-menu").getAttribute("class");
  return Boolean(cls && cls.includes("open"));
}

test.describe("Мобильное меню", () => {
  test("открытие и переход по пункту", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/");
    const toggle = page.locator(".menu-toggle");
    await expect(toggle).toBeVisible();
    expect(await menuOpen(page)).toBe(false);

    await toggle.click();
    expect(await menuOpen(page)).toBe(true);
    await Promise.all([
      page.waitForURL("/settings"),
      page.locator('#user-menu a[href="/settings"]').first().click(),
    ]);
    await expect(page.locator("h1")).toHaveText("Настройки пользователя");
  });

  test("закрытие повторным кликом", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/");
    const toggle = page.locator(".menu-toggle");
    await toggle.click();
    expect(await menuOpen(page)).toBe(true);
    await toggle.click();
    expect(await menuOpen(page)).toBe(false);
  });

  test("закрытие по Escape", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/");
    const toggle = page.locator(".menu-toggle");
    await toggle.click();
    expect(await menuOpen(page)).toBe(true);
    await page.keyboard.press("Escape");
    expect(await menuOpen(page)).toBe(false);
  });

  const PAGES = ["/", "/securities/AFLT", "/macro", "/indicators", "/login", "/register"];
  for (const path of PAGES) {
    test(`рендер без console-ошибок: ${path}`, async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error") errors.push(msg.text());
      });
      await page.goto(path);
      await page.waitForLoadState("domcontentloaded");
      expect(errors).toEqual([]);
    });
  }
});
