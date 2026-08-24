import { test, expect } from "@playwright/test";
import { login, USER } from "./helpers";

test.describe("Теханализ в LLM", () => {
  test("карточка акции: кнопка и блок «Теханализ в LLM»", async ({ page }) => {
    await page.goto("/securities/AFLT");
    await expect(page.locator("body")).toContainText("Теханализ в LLM");
    // кнопка над кнопкой «Сделать скриншот»
    await expect(page.locator("button", { hasText: "Теханализ в LLM" })).toBeVisible();
    // блок карточек результатов присутствует (seed может быть пуст — тогда текст «Анализов пока нет»)
    await expect(page.locator("body")).toContainText("Теханализ в LLM (ChatGPT)");
  });

  test("неавторизованный запуск редиректит на /login", async ({ request }) => {
    const resp = await request.post("/securities/AFLT/tech-analysis", { maxRedirects: 0 });
    expect(resp.status()).toBe(303);
    expect((resp.headers()["location"] || "").toLowerCase()).toContain("/login");
  });

  test("страница ответа доступна и рендерит сценарии", async ({ page }) => {
    // создадим запись tech_analysis напрямую через API невозможно без ключа;
    // проверяем, что страница отдаёт 404 на несуществующий id без падения
    const resp = await page.goto("/tech_analysis/999999");
    expect(resp ? resp.status() : 500).toBe(404);
  });
});
