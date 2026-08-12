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
  });
});
