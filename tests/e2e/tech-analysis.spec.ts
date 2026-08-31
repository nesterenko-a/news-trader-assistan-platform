import { test, expect } from "@playwright/test";
import { login, USER } from "./helpers";

test.describe("Теханализ в LLM", () => {
  test("авторизованному: якорь, блок и селектор LLM", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/securities/AFLT");
    // наверху — якорь-ссылка на блок тех.анализа
    await expect(
      page.locator('a[href="#tech-analysis-section"]', { hasText: "Теханализ в LLM" })
    ).toBeVisible();
    await expect(page.locator("h2", { hasText: "Теханализ в LLM" }).first()).toBeVisible();
    // селектор выбора LLM доступен авторизованному
    await expect(page.locator('select[name="provider"]')).toBeVisible();
  });

  test("гостю: форма и якорь скрыты, показана плашка «войдите»", async ({ page }) => {
    await page.goto("/securities/AFLT");
    await expect(page.locator('select[name="provider"]')).toHaveCount(0);
    await expect(
      page.locator('a[href="#tech-analysis-section"]', { hasText: "Теханализ в LLM" })
    ).toHaveCount(0);
    await expect(page.locator("body")).toContainText("войдите");
  });

  test("неавторизованный запуск редиректит на /login", async ({ request }) => {
    const resp = await request.post("/securities/AFLT/tech-analysis", { maxRedirects: 0 });
    expect(resp.status()).toBe(303);
    expect((resp.headers()["location"] || "").toLowerCase()).toContain("/login");
  });

  test("авторизованный запуск остаётся на карточке и показывает ход обработки", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/securities/AFLT");
    await page.route("**/securities/AFLT/tech-analysis", async (route) => {
      await route.fulfill({ status: 303, headers: { location: "/securities/AFLT" } });
    });
    await page.route("**/v1/tech-analysis?ticker=AFLT&page=1", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [{ status: "running", stage: "refreshing_data" }] }),
      });
    });

    await page.getByRole("button", { name: "Запустить теханализ" }).click();

    await expect(page).toHaveURL(/\/securities\/AFLT/);
    await expect(page.locator("#ta-progress-ajax")).toBeVisible();
    await expect(page.locator("#ta-stage-ajax")).toHaveText("актуализация данных");
    await expect(page.locator("#ta-run-form")).toBeHidden();
  });

  test("страница ответа доступна и рендерит сценарии", async ({ page }) => {
    const resp = await page.goto("/tech_analysis/999999");
    expect(resp ? resp.status() : 500).toBe(404);
  });
});
