import { test, expect } from "@playwright/test";

test.describe("Публичные страницы", () => {
  test("главная: рендер и поиск → карточка", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("h1")).toContainText("Аналитика на основе новостного фона");
    await page.fill('input[name="ticker"]', "AFLT");
    await Promise.all([
      page.waitForURL("**/securities/AFLT"),
      page.click('form.search button[type="submit"]'),
    ]);
    await expect(page.locator("body")).toContainText("AFLT");
  });

  test("главная: фильтр по сектору", async ({ page }) => {
    await page.goto("/");
    await page.selectOption('select[name="sector"]', "Авиаперевозки");
    await page.waitForURL("**sector=*");
    const rows = await page.locator("table.table tbody").innerText();
    expect(rows).toContain("AFLT");
    expect(rows).not.toContain("LKOH");
  });

  test("главная: фильтр по типу", async ({ page }) => {
    await page.goto("/");
    // дефолт — all, сначала переключаем на stocks (радио скрыты — кликаем по label)
    await page.locator('label:has(input[name="type"][value="stocks"])').click();
    await page.waitForURL("**type=stocks*");
    await page.locator('label:has(input[name="type"][value="all"])').click();
    await page.waitForURL("**type=all*");
    const rows = await page.locator("table.table tbody").innerText();
    expect(rows).toContain("AFLT");
    expect(rows).toContain("LKOH");
  });

  test("карточка: график и диапазоны", async ({ page }) => {
    await page.goto("/securities/AFLT");
    await expect(page.locator("h2").first()).toContainText("История цены");
    // сидинг содержит свечи — график рендерится
    await expect(page.locator("body")).not.toContainText("Нет исторических данных");
    await expect(page.locator("svg.chart polyline").first()).toBeVisible();
    await expect(page.locator("body")).toContainText("Профиль объёма (Volume Profile)");

    const rangeLinks = page.locator(".chart-range").first().locator("a");
    await expect(rangeLinks).toHaveText([
      "1 день",
      "7 дней",
      "1 год",
      "5 лет",
      "Всё",
    ]);
    await Promise.all([
      page.waitForURL("**/securities/AFLT?range=1y*"),
      page.locator(".chart-range").first().locator("a:has-text('1 год')").click(),
    ]);
    await expect(page.locator(".chart-range").first().locator("a.active")).toHaveText("1 год");
  });

  test("карточка: кнопка «Сделать скриншот» без JS-ошибок", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(String(err)));
    await page.goto("/securities/AFLT");
    const button = page.getByRole("button", { name: "Сделать скриншот" });
    await expect(button).toBeVisible();
    await button.click();
    await page.waitForTimeout(500);
    expect(errors).toEqual([]);
  });

  test("карточка: новости из сидинга", async ({ page }) => {
    await page.goto("/securities/SBER");
    await expect(page.locator("body")).toContainText("Новости (1)");
    await expect(page.locator("body")).toContainText("Сбербанк отчитался о росте прибыли");
    await expect(page.locator(".news-item")).toHaveCount(1);
  });

  test("карточка: вердикт и сигналы", async ({ page }) => {
    await page.goto("/securities/SBER");
    const badge = page.locator("div.badge").first();
    await expect(badge).toBeVisible();
    const verdict = (await badge.innerText()).trim();
    expect(["BUY", "SELL", "HOLD", "INSUFFICIENT_DATA"]).toContain(verdict);
    await expect(page.locator("body")).toContainText("Сигналы");
  });

  test("карточка: карта зависимостей и её рендер", async ({ page }) => {
    await page.goto("/securities/SBER");
    await expect(page.locator("body")).toContainText("Карта зависимостей");
    // SVG-карта присутствует
    await expect(page.locator(".depmap")).toHaveCount(1);
  });

  test("/map: открывается и отрисовывает интерактивную карту (Cytoscape)", async ({ page }) => {
    await page.goto("/map?ticker=AFLT");
    await expect(page.locator("body")).toContainText("Карта зависимостей");
    // Cytoscape рендерит стопку canvas-слоёв внутри #cy
    await expect(page.locator("#cy canvas")).not.toHaveCount(0, { timeout: 8000 });
  });

  test("карточка: неизвестный тикер — 404", async ({ page }) => {
    const response = await page.goto("/securities/XXXX");
    expect(response?.status()).toBe(404);
  });

  test("/macro: календарь и фильтры", async ({ page }) => {
    await page.goto("/macro");
    await expect(page.locator("h1")).toContainText("Макрокалендарь");
    await expect(page.locator('select[data-filter="region"]')).toHaveCount(1);
    await expect(page.locator('select[data-filter="impact"]')).toHaveCount(1);
    await expect(page.locator('select[data-filter="scope"]')).toHaveCount(1);

    const visible = page.locator("#macro-table tbody tr:visible");
    await expect(visible).toHaveCount(3);
    await page.selectOption('select[data-filter="region"]', "RU");
    await expect(visible).toHaveCount(2);
    await page.selectOption('select[data-filter="impact"]', "high");
    await expect(visible).toHaveCount(1);
  });

  const TABS: Array<[string, string, string]> = [
    ["ema", "EMA (скользящие средние)", "SBER"],
    ["macd", "MACD", "SBER"],
    ["oi", "Открытый интерес (OI)", ""],
    ["volume_profile", "Профиль объёма (Volume Profile)", ""],
    ["support_resistance", "Поддержка/сопротивление", ""],
  ];
  for (const [name, label, ticker] of TABS) {
    test(`индикаторы: вкладка ${name}`, async ({ page }) => {
      const url = ticker ? `/indicators?name=${name}&ticker=${ticker}` : `/indicators?name=${name}`;
      await page.goto(url);
      await expect(page.locator(".seg-item.seg-active")).toContainText(label);
      await expect(page.locator('button[type="submit"]')).toHaveCount(1);
      if (name === "ema" || name === "macd") {
        await expect(page.locator("h2").first()).toContainText("Сбербанк (SBER)");
        await expect(page.locator("svg.chart").first()).toBeVisible();
      }
      if (name === "macd") {
        // на сидовых свечах MACD даёт сигналы
        const rows = page.locator("table.table tbody tr");
        expect(await rows.count()).toBeGreaterThan(0);
      }
    });
  }

  const DATA_TABS: Array<[string, string | null]> = [
    ["volume_profile", "Профиль объёма — Сбербанк (SBER)"],
    ["support_resistance", null],
  ];
  for (const [name, heading] of DATA_TABS) {
    test(`индикаторы: ${name} с тикером SBER`, async ({ page }) => {
      await page.goto(`/indicators?name=${name}&ticker=SBER`);
      await expect(page.locator("body")).not.toContainText("Бумага не найдена");
      await expect(page.locator("body")).toContainText("Сбербанк (SBER)");
      if (heading) await expect(page.locator("h2").first()).toContainText(heading);
    });
  }
});
