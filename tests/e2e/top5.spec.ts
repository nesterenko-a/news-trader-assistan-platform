import { test, expect } from "@playwright/test";
import { login, USER } from "./helpers";

test.describe("Top-5: лучшая сделка из шаблона акций", () => {
  test("шаблон из 30 акций доступен целиком для отбора", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/top5");
    await page.locator("a.top5-tpl-card", { hasText: "e2e_top5_thirty" }).click();
    await expect(page.locator("body")).toContainText("Состав шаблона — 30 инструментов · Группы по 10");
    await expect(page.locator("#tpl-cloud .tpl-tag")).toHaveCount(30);
    await expect(page.locator("#btn-run")).toBeEnabled();
    await expect(page.locator("#top5-count")).toHaveText("30");
    await expect(page.locator(".top5-row:visible")).toHaveCount(5);
  });

  test("ошибочный анализ повторяется в строке без перезагрузки", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/top5");
    await page.locator("a.top5-tpl-card", { hasText: "e2e_top5_retry_future" }).click();
    await expect(page.locator("h2", { hasText: "e2e_top5_retry_future" })).toBeVisible();
    await page.locator("[data-batch-toggle]").first().click();
    const row = page.locator("tr[data-analysis-id]", { hasText: "FUTRETRY" });
    const batchId = await row.getAttribute("data-batch-id");
    const analysisId = await row.getAttribute("data-analysis-id");
    await page.route(`**/v1/top5/${batchId}/items/${analysisId}/retry`, route => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({ id: Number(analysisId), status: "running", stage: "refreshing_data" }),
    }));
    let polls = 0;
    await page.route(`**/v1/top5/${batchId}/status`, route => {
      polls += 1;
      const success = polls > 1;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        batch_id: Number(batchId), status: success ? "success" : "running", total: 1,
        done: success ? 1 : 0, success: success ? 1 : 0, running: success ? 0 : 1, failed: 0,
        items: [{ id: Number(analysisId), ticker: "FUTRETRY", status: success ? "success" : "running", stage: success ? "done" : "awaiting_llm", verdict: success ? "BUY" : null, error: null }],
      }) });
    });
    const url = page.url();
    let navigations = 0;
    page.on("framenavigated", frame => { if (frame === page.mainFrame()) navigations += 1; });
    await row.locator(".batch-retry").click();
    await expect(row.locator(".batch-item-percent")).toHaveText("80%");
    await expect(row.locator(".top5-spinner")).toBeVisible();
    await expect(row.locator(".batch-item-action a")).toHaveText("разбор →", { timeout: 8000 });
    await expect(row.locator(".batch-item-verdict")).toHaveText("BUY");
    expect(page.url()).toBe(url);
    expect(navigations).toBe(0);
  });

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
      await expect(page.locator("#top5-table thead")).toContainText("Вход ≈");
      await expect(page.locator("#top5-table thead")).toContainText("Цель 1");
      await expect(page.locator("#top5-table thead")).not.toContainText("Стоп");
      await expect(row.locator(".top5-entry-reference")).not.toContainText(/[-–—]\s*\d/);
      await expect(row.locator(".top5-expand-cell")).toBeVisible();
      await expect(row.locator(".top5-expand-icon")).toHaveText("⌄");
      await expect(row).toHaveAttribute("aria-expanded", "false");
      await row.click();
      await expect(page.locator(".top5-detail").first()).toBeVisible();
      await expect(row).toHaveAttribute("aria-expanded", "true");
      const detail = page.locator(".top5-detail").first();
      await expect(detail.locator(".top5-level-track")).toBeVisible();
      await expect(detail.locator(".top5-level-entry strong")).not.toBeEmpty();
      await expect(detail.locator(".top5-level-stop strong")).not.toBeEmpty();
      await expect(detail.locator(".top5-level-target").first()).toBeVisible();
      await expect(detail.locator(".top5-deal-panel")).toBeVisible();
      await expect(detail.locator(".top5-scenario-panel")).toBeVisible();
      await expect(detail.locator(".top5-analysis-panel")).toBeVisible();
      await expect(detail.locator(".top5-open-analysis")).toHaveText("Открыть разбор →");
      await expect(detail.locator(".top5-open-analysis")).toHaveAttribute("href", /\/tech_analysis\/\d+/);
      await expect(detail.locator(".top5-scenario-panel h3")).toContainText(/^Сценарий [ABC]$/);
      await expect(detail.getByText("Итоговая оценка")).toHaveCount(0);
      await expect(detail.getByText("Сценарий B")).toHaveCount(0);
      await expect(detail.getByText("Сценарий C")).toHaveCount(0);
      const columns = await detail.locator(".top5-deal-panel").evaluate(panel => getComputedStyle(panel).gridTemplateColumns.split(" ").map(parseFloat));
      expect(columns[0]).toBeGreaterThan(columns[1] * 1.7);
      await row.focus();
      await page.keyboard.press("Enter");
      await expect(page.locator(".top5-detail").first()).toBeHidden();
    }
  });

  test("фильтр качества пересчитывает весь набор и поднимает шестого кандидата", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/top5");
    await page.locator("a.top5-tpl-card", { hasText: "e2e_top5_filter" }).click();
    await expect(page.locator("#top5-count")).toHaveText("6");
    await expect(page.locator(".top5-row:visible")).toHaveCount(5);
    await expect(page.locator(".top5-row", { hasText: "FILT5" })).toBeHidden();

    await page.locator("#top5-filter-on").check();
    await expect(page.locator("#top5-qualified")).toHaveText("4");
    await expect(page.locator(".top5-row", { hasText: "FILT5" })).toBeVisible();
    await expect(page.locator(".top5-row", { hasText: "SBER" })).toBeHidden();
    await expect(page.locator(".top5-row", { hasText: "FILT4" })).toBeHidden();

    await page.locator("#top5-filter-risk").fill("1.5");
    await expect(page.locator("#top5-qualified")).toHaveText("0");
    await expect(page.locator("#top5-empty")).toBeVisible();
    await page.locator("#top5-filter-reset").click();
    await expect(page.locator("#top5-qualified")).toHaveText("6");
    await expect(page.locator(".top5-row:visible")).toHaveCount(5);

    await page.locator("#top5-filter-liquidity-on").check();
    await expect(page.locator("#top5-qualified")).toHaveText("1");
    await expect(page.locator(".top5-row", { hasText: "SBER" })).toBeVisible();
    await page.locator("#top5-filter-liquidity-on").uncheck();
    await page.locator("#top5-filter-plan").check();
    await expect(page.locator("#top5-qualified")).toHaveText("1");
    await page.locator("#top5-filter-plan").uncheck();
    await page.locator("#top5-filter-direction").selectOption("SHORT");
    await expect(page.locator("#top5-qualified")).toHaveText("1");
    await expect(page.locator(".top5-row", { hasText: "FILT3" })).toBeVisible();
    await page.locator(".top5-row", { hasText: "FILT3" }).click();
    await expect(page.locator(".top5-detail:visible")).toHaveCount(1);
    await page.setViewportSize({ width: 375, height: 760 });
    await expect(page.locator("#top5-quality")).toBeVisible();
    const panel = await page.locator("#top5-quality").boundingBox();
    expect(panel).not.toBeNull();
    expect(panel!.x + panel!.width).toBeLessThanOrEqual(375);
  });
});
