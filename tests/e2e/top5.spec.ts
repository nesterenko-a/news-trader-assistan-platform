import { test, expect } from "@playwright/test";
import { login, USER } from "./helpers";

test.describe("Top-5: лучшая сделка из шаблона акций", () => {
  test("авторизованному: страница с шаблоном и подборкой Top-5", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/top5");
    await expect(page.locator("h1", { hasText: "Top 5" }).first()).toBeVisible();
    // выбор шаблона — карточки (клик = выбор шаблона)
    await expect(page.locator("a.top5-tpl-card").first()).toBeVisible();
    await page.locator("a.top5-tpl-card", { hasText: "e2e_top5" }).first().click();
    await page.waitForLoadState("domcontentloaded");
    await expect(page.locator("h2", { hasText: "e2e_top5" })).toBeVisible();
    // селектор выбора LLM (Авто/ChatGPT/DeepSeek) по аналогии с одиночным анализом
    await expect(page.locator("label", { hasText: "LLM для анализа" })).toBeVisible();
    await expect(page.locator("#btn-provider")).toBeVisible();
    // облако тикеров «Состав шаблона»
    await expect(page.locator("#tpl-cloud")).toBeVisible();
    await expect(page.locator(".tpl-tag").first()).toBeVisible();
    // приоритет LLM сохраняется между перезагрузками (cookie)
    await expect(page.locator("#tpl-priority")).toHaveText("Авто");
    await page.selectOption("#btn-provider", "deepseek");
    await expect(page.locator("#tpl-priority")).toHaveText("DeepSeek");
    await page.reload();
    await page.waitForLoadState("domcontentloaded");
    await expect(page.locator("#btn-provider")).toHaveValue("deepseek");
    await expect(page.locator("#tpl-priority")).toHaveText("DeepSeek");
    // приводим приоритет к дефолту, чтобы не влиять на прочие прогоны
    await page.selectOption("#btn-provider", "");
    // история запусков батчей по шаблону (из сидинга)
    await expect(page.locator("h2", { hasText: "История запусков Top-5" })).toBeVisible();
    await expect(page.locator("[data-batch-toggle]").first()).toBeVisible();
    // раскрытие деталей батча: собственный Top-5 батча + состав по акциям
    await page.locator("[data-batch-toggle]").first().click();
    await expect(page.locator(".batch-detail").first()).toBeVisible();
    await expect(page.locator(".batch-detail").first()).toContainText("Лучшие возможности");
    await expect(page.locator(".batch-detail").first()).toContainText("Состав батча");
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
    await page.locator("a.top5-tpl-card", { hasText: "e2e_top5" }).first().click();
    await page.waitForLoadState("domcontentloaded");
    const row = page.locator(".top5-row").first();
    if (await row.count()) {
      await row.click();
      await expect(page.locator(".top5-detail").first()).toBeVisible();
    }
  });
});
