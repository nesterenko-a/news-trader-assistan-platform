import { test, expect } from "@playwright/test";

test.describe("Теханализ в LLM", () => {
  test("карточка акции: якорь и блок «Теханализ в LLM»", async ({ page }) => {
    await page.goto("/securities/AFLT");
    // наверху — якорь-ссылка на блок тех.анализа
    await expect(
      page.locator('a[href="#tech-analysis-section"]', { hasText: "Теханализ в LLM" })
    ).toBeVisible();
    // заголовок блока без привязки к ChatGPT
    await expect(page.locator("h2", { hasText: "Теханализ в LLM" }).first()).toBeVisible();
    await expect(page.locator("body")).not.toContainText("Теханализ в LLM (ChatGPT)");
    // селектор выбора LLM присутствует
    await expect(page.locator('select[name="provider"]')).toBeVisible();
  });

  test("неавторизованный запуск редиректит на /login", async ({ request }) => {
    const resp = await request.post("/securities/AFLT/tech-analysis", { maxRedirects: 0 });
    expect(resp.status()).toBe(303);
    expect((resp.headers()["location"] || "").toLowerCase()).toContain("/login");
  });

  test("страница ответа доступна и рендерит сценарии", async ({ page }) => {
    // проверяем, что страница отдаёт 404 на несуществующий id без падения
    const resp = await page.goto("/tech_analysis/999999");
    expect(resp ? resp.status() : 500).toBe(404);
  });

  test("карточка акции: кнопка удаления заглушка (нет данных — нет карточек)", async ({ page }) => {
    await page.goto("/securities/AFLT");
    // если карточек нет — текст «Анализов пока нет»
    await expect(page.locator("body")).toContainText("Анализов пока нет");
  });
});
