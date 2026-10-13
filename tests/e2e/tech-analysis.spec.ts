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

  test("оглавление и схема подтверждений переключают сценарии", async ({ page }) => {
    await login(page, USER.username, USER.password);
    const response = await page.request.get("/v1/tech-analysis?ticker=SBER&page=1");
    const data = await response.json();
    const analysis = data.items.find((item: { model: string }) => item.model === "e2e");
    expect(analysis).toBeTruthy();
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`/tech_analysis/${analysis.id}`);

    await expect(page.locator(".tech-outline")).toBeVisible();
    await expect(page.locator(".md-body h2")).toHaveCount(3);
    await expect(page.locator(".md-body h2").nth(1)).toHaveAttribute("id", "tech-section-1");
    await expect(page.locator(".md-body h2").nth(2)).toHaveAttribute("id", "tech-section-2");
    await expect(page.locator(".md-body h2").nth(1).locator("+ #confirmation-block")).toBeVisible();
    await expect(page.locator("#confirmation-panel-a")).toContainText("1 закрытая свеча");
    await expect(page.locator("#confirmation-panel-a")).toContainText("объём растёт");
    await page.locator(".tech-outline").getByRole("link", { name: "10. Что должно подтвердить сделку" }).click();
    await expect(page).toHaveURL(/#tech-section-1$/);

    await page.getByRole("tab", { name: "Сценарий B" }).click();
    await expect(page.locator("#confirmation-panel-b")).toBeVisible();
    await expect(page.locator("#confirmation-panel-b")).toContainText("продажа");
    await expect(page.locator("#confirmation-panel-b")).toContainText("не указано");
    await expect(page.locator("#confirmation-panel-a")).toBeHidden();

    await page.setViewportSize({ width: 375, height: 760 });
    await expect(page.locator(".tech-outline-mobile")).toBeVisible();
    await page.locator(".tech-outline-mobile summary").click();
    await expect(page.locator("#tech-outline-mobile-links")).toContainText("Что должно подтвердить сделку");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  });

  test("старый ответ с неполным JSON сохраняет текст и оглавление", async ({ page }) => {
    await login(page, USER.username, USER.password);
    const response = await page.request.get("/v1/tech-analysis?ticker=AFLT&page=1");
    const data = await response.json();
    const analysis = data.items.find((item: { model: string }) => item.model === "e2e-legacy");
    expect(analysis).toBeTruthy();
    await page.goto(`/tech_analysis/${analysis.id}`);
    await expect(page.locator("#confirmation-block")).toContainText("схема недоступна");
    await expect(page.locator(".md-body")).toContainText("Ждать подтверждения без заданного срока");
    await expect(page.locator("#tech-outline-mobile-links")).toContainText("Что должно подтвердить сделку");
    await expect(page.locator("[data-confirmation-tab]")).toHaveCount(0);
    const markdown = await page.request.get(`/v1/tech-analysis/${analysis.id}/response.md`);
    expect(await markdown.text()).toBe("## 10. Что должно подтвердить сделку\nЖдать подтверждения без заданного срока.");
  });
});
