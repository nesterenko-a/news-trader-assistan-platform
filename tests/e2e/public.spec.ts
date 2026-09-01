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

  test("карточка: карта зависимостей (Cytoscape) на карточке бумаги", async ({ page }) => {
    await page.goto("/securities/SBER");
    await expect(page.locator("body")).toContainText("Карта зависимостей");
    // Cytoscape рендерит canvas-слои внутри #cy-security
    await expect(page.locator("#cy-security canvas")).not.toHaveCount(0, { timeout: 8000 });
    // фильтр по силе влияния
    await expect(page.locator("#cy-security-strength")).toHaveCount(1);
  });

  test("/map: открывается и отрисовывает интерактивную карту (Cytoscape)", async ({ page }) => {
    await page.goto("/map?ticker=AFLT");
    await expect(page.locator("body")).toContainText("Карта зависимостей");
    // Cytoscape рендерит стопку canvas-слоёв внутри #cy
    await expect(page.locator("#cy canvas")).not.toHaveCount(0, { timeout: 8000 });
    // фильтр по силе влияния
    await expect(page.locator("#cy-strength")).toHaveCount(1);
  });

  test("/map: поиск бумаги из тикер-листа только для акций", async ({ page }) => {
    await page.goto("/map");
    // дата-фильтр акций в datalist автодополнения (как в Аналитике)
    await expect(page.locator('#map-tickers-datalist[data-filter="stocks"]')).toHaveCount(1);
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
    ["bollinger", "Полосы Боллинджера", "SBER"],
    ["atr", "ATR (средний истинный диапазон)", "SBER"],
    ["adx", "ADX / DI", "SBER"],
  ];
  for (const [name, label, ticker] of TABS) {
    test(`индикаторы: вкладка ${name}`, async ({ page }) => {
      const url = ticker ? `/indicators?name=${name}&ticker=${ticker}` : `/indicators?name=${name}`;
      await page.goto(url);
      await expect(page.locator(".indicator-nav-link.active")).toContainText(label);
      await expect(page.locator('button[type="submit"]')).toHaveCount(1);
      if (["ema", "macd", "bollinger", "atr", "adx"].includes(name)) {
        await expect(page.locator("h2").first()).toContainText("Сбербанк (SBER)");
        await expect(page.locator("svg.chart").first()).toBeVisible();
      }
      if (["macd", "adx"].includes(name)) {
        // на сидовых свечах MACD/ADX дают сигналы (трендовые сценарии)
        const rows = page.locator("table.table tbody tr");
        expect(await rows.count()).toBeGreaterThan(0);
      }
    });
  }

  test("индикаторы: мобильный селектор без каталога", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/indicators?name=oi");
    await expect(page.locator(".indicators-catalog")).toBeHidden();
    const select = page.locator("#indicator-select");
    await expect(select).toBeVisible();
    await select.selectOption({ value: "/indicators?name=volume_profile" });
    await expect(page).toHaveURL(/\/indicators\?name=volume_profile/);
    await expect(select).toHaveValue("/indicators?name=volume_profile");
  });

  test("индикаторы: позиции групп клиентов читаемы и сортируются", async ({ page }) => {
    await page.goto("/indicators?name=oi&ticker=SBER");
    const rows = page.locator(".client-group-row");
    await expect(rows).toHaveCount(6);
    await expect(page.locator("#cg-table")).toHaveCount(0);
    await expect(rows.first().getByText("Физические лица")).toBeVisible();
    await expect(rows.first().getByText("Юридические лица")).toBeVisible();
    await page.locator("#cg-sort").selectOption("ph_net-asc");
    const firstNet = await rows.first().getAttribute("data-ph-net");
    const lastNet = await rows.last().getAttribute("data-ph-net");
    expect(Number(firstNet)).toBeLessThanOrEqual(Number(lastNet));
    await page.setViewportSize({ width: 375, height: 667 });
    await page.reload();
    await expect(page.locator(".client-group-row").first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  });

  test("индикаторы: OI с пустым периодом берёт месяц, Basis не падает", async ({ page }) => {
    await page.goto("/indicators?name=oi&ticker=SBER&from=&to=");
    await expect(page.locator('input[name="from"]')).not.toHaveValue("");
    await expect(page.locator('input[name="to"]')).not.toHaveValue("");
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.reload();
    await expect(page.locator(".indicators-outline")).toBeVisible();
    await expect(page.locator("#indicator-outline-links a").first()).toBeVisible();
    const guide = page.locator("#oi-guide");
    await expect(guide).not.toHaveAttribute("open", "");
    await page.locator('#indicator-outline-links a', { hasText: 'Как читать сигналы' }).click();
    await expect(guide).toHaveAttribute("open", "");
    await page.goto("/indicators?name=basis&ticker=SBER");
    await expect(page).toHaveTitle(/Индикаторы/);
  });

  const DATA_TABS: Array<[string, string | null]> = [
    ["volume_profile", "Профиль объёма — Сбербанк (SBER)"],
    ["support_resistance", null],
    ["bollinger", "Полосы Боллинджера — Сбербанк (SBER)"],
    ["atr", "ATR — Сбербанк (SBER)"],
    ["adx", "ADX/DI — Сбербанк (SBER)"],
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
