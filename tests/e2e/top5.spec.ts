import { test, expect } from "@playwright/test";
import { login, USER } from "./helpers";

test.describe("Top-5: лучшая сделка из шаблона акций", () => {
  test("авторизованному: страница с шаблоном и подборкой Top-5", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/top5");
    await expect(page.locator("h1", { hasText: "Top 5" }).first()).toBeVisible();
    await expect(page.locator('select[name="template_id"]')).toBeVisible();
    // выбираем сидовый шаблон акций и смотрим результат
    await page.selectOption('select[name="template_id"]', { label: "e2e_top5 (SBER)" });
    await page.click('button[type="submit"]');
    await page.waitForLoadState("domcontentloaded");
    await expect(page.locator("h2", { hasText: "e2e_top5" })).toBeVisible();
    // селектор выбора LLM (Авто/ChatGPT/DeepSeek) по аналогии с одиночным анализом
    await expect(page.locator("label", { hasText: "LLM для анализа" })).toBeVisible();
    await expect(page.locator("#btn-provider")).toBeVisible();
    // в подборке должны быть SBER и отрендеренный Expected R сценария (0.65×2.5−0.35×1=1.275→"1.27")
    await expect(page.locator("td", { hasText: "SBER" }).first()).toBeVisible();
    await expect(page.locator("body")).toContainText("1.27");
  });

  test("гостю: /top5 редиректит на /login", async ({ page }) => {
    await page.goto("/top5");
    expect(page.url()).toContain("/login");
  });

  test("раскрытие сценариев по клику на строку", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/top5");
    await page.selectOption('select[name="template_id"]', { label: "e2e_top5 (SBER)" });
    await page.click('button[type="submit"]');
    await page.waitForLoadState("domcontentloaded");
    const row = page.locator(".top5-row").first();
    if (await row.count()) {
      await row.click();
      await expect(page.locator(".top5-detail").first()).toBeVisible();
    }
  });
});
