import { test, expect } from "@playwright/test";
import { ADMIN, USER, login } from "./helpers";

test.describe("News manager и админка", () => {
  test("news: невалидный URL — ошибка", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/news");
    const add = page.locator('form[action="/news/rss/add"]');
    await add.locator('input[name="name"]').fill("Bad Feed");
    await add.locator('input[name="url"]').fill("ftp://example.com/rss");
    await Promise.all([page.waitForURL("**/news*"), add.locator('button[type="submit"]').click()]);
    await expect(page.locator("body")).toContainText("Допустимы только http/https");
  });

  test("news: добавить → toggle LLM → удалить", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/news");
    // уникальное имя — устойчивость к повторным прогонам/UI-режиму
    const feedName = "E2E Feed " + crypto.randomUUID().replace(/-/g, "").slice(0, 6);
    const add = page.locator('form[action="/news/rss/add"]');
    await add.locator('input[name="name"]').fill(feedName);
    await add.locator('input[name="url"]').fill("https://example.com/rss");
    await Promise.all([page.waitForURL("/news"), add.locator('button[type="submit"]').click()]);

    const row = page.locator(`tr:has-text('${feedName}')`);
    await expect(row).toHaveCount(1);

    // toggle «LLM-разбор» (async POST)
    const checkbox = row.locator('input[data-field="use_llm"]');
    await Promise.all([page.waitForResponse("**/news/rss/toggle"), checkbox.check()]);
    await page.reload();
    await expect(row.locator('input[data-field="use_llm"]')).toBeChecked();

    // удаление
    await Promise.all([page.waitForURL("/news"), row.locator("button.feed-remove").click()]);
    await expect(page.locator(`tr:has-text('${feedName}')`)).toHaveCount(0);
  });

  test("news: «Вернуть стандартные ленты»", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/news");
    await Promise.all([
      page.waitForURL("/news"),
      page.click('form[action="/news/rss/restore"] button[type="submit"]'),
    ]);
    expect(await page.locator("table.table tbody tr").count()).toBeGreaterThan(0);
  });

  test("sites: вкладка рендерит сайт из списка", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/news?tab=sites");
    await expect(page.locator(".tab-active")).toContainText("Сайты");
    const row = page.locator('tr:has-text("e2e_site")');
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("✔ работает");
    await expect(page.locator('button:has-text("Вернуть стандартные сайты")')).toHaveCount(1);
  });

  test("sites: невалидный URL — ошибка", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/news?tab=sites");
    const add = page.locator('form[action="/news/site/add"]');
    await add.locator('input[name="name"]').fill("Bad Site");
    await add.locator('input[name="url"]').fill("ftp://example.com/press");
    await Promise.all([page.waitForURL("**/news*"), add.locator('button[type="submit"]').click()]);
    await expect(page.locator("body")).toContainText("Допустимы только http/https");
  });

  test("sites: toggle LLM → удалить", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/news?tab=sites");
    const row = page.locator('tr:has-text("e2e_site")');
    await expect(row).toHaveCount(1);
    // toggle «LLM-разбор» (async POST; e2e.example не резолвится — проверка быстрая)
    const checkbox = row.locator('input[data-field="use_llm"]');
    await Promise.all([page.waitForResponse("**/news/site/toggle"), checkbox.check()]);
    await page.reload();
    await expect(page.locator('tr:has-text("e2e_site") input[data-field="use_llm"]')).toBeChecked();
    // удаление
    await Promise.all([
      page.waitForURL("**/news*"),
      page.locator('tr:has-text("e2e_site") button.feed-remove').click(),
    ]);
    await expect(page.locator('tr:has-text("e2e_site")')).toHaveCount(0);
  });

  test("sites: «Вернуть стандартные сайты»", async ({ page }) => {
    await login(page, USER.username, USER.password);
    await page.goto("/news?tab=sites");
    await Promise.all([
      page.waitForURL("**/news*"),
      page.click('form[action="/news/site/restore"] button[type="submit"]'),
    ]);
    await expect(page.locator("body")).toContainText("Добавлено стандартных сайтов");
  });

  test("admin: запуск скрипта, статус и вывод по AJAX", async ({ page }) => {
    await login(page, ADMIN.username, ADMIN.password);
    await page.goto("/admin");
    const form = page.locator(
      'form[action="/admin/scripts/run"]:has(input[name="script"][value="seed_db"])'
    );
    await expect(form).toHaveCount(1);
    await Promise.all([page.waitForURL("**/admin/runs/*"), form.locator('button[type="submit"]').click()]);
    await expect(page.locator("body")).toContainText("Наполнить справочники");
    await expect(page.locator("#run-live")).toHaveCount(1);
    // скрипт завершается, статус и вывод подтягиваются AJAX (partial)
    await page.waitForSelector(".run-status:not(.run-running)", { timeout: 30_000 });
    await expect(page.locator(".run-status")).toHaveText(/успех|ошибка/);
    // вывод подтягивается асинхронно после смены статуса — ждём непустого лога
    const out = page.locator("pre.run-output");
    await expect(out).toHaveCount(1);
    await expect(out).not.toBeEmpty({ timeout: 15_000 });
  });

  test("admin: дополнение графа — страница и добавление ссылки", async ({ page }) => {
    await login(page, ADMIN.username, ADMIN.password);
    await page.goto("/admin/graph");
    await expect(page.locator("body")).toContainText("Дополнение графа");
    await expect(page.locator('form[action="/admin/graph/add"]')).toHaveCount(3);
    // секция «Граф влияния» — textarea для вставки ASCII-схемы и кнопка применения
    await expect(page.locator('textarea[name="graph"]')).toHaveCount(1);
    await expect(page.locator('button[type="submit"]:has-text("Применить граф")')).toHaveCount(1);
    // таблица связей: пагинация и редактирование/удаление (по образцу «Новостей»)
    await expect(page.locator('form[action*="/delete"]').first()).toHaveCount(1);
    await expect(page.locator('.graph-edit').first()).toHaveCount(1);
    await expect(page.locator('.graph-field').first()).toHaveCount(1);

    // Добавить новое ребро ссылкой через форму многих ссылок
    const suffix = crypto.randomUUID().replace(/-/g, "").slice(0, 6);
    await page.fill('input[name="from_name"]', "Нефть" + suffix);
    await page.fill('input[name="to_name"]', "Сектор" + suffix);
    await page.fill('input[name="url"]', "https://example.com/" + suffix);
    const linksForm = page.locator('form[action="/admin/graph/add"][id="links-form"]').first();
    await Promise.all([
      page.waitForURL("**/admin/runs/*"),
      linksForm.locator('button[type="submit"]:has-text("Применить ссылки")').click(),
    ]);
    await expect(page.locator("#run-live")).toHaveCount(1);
  });


  test("admin: шаблоны инструментов — создание и удаление", async ({ page }) => {
    await login(page, ADMIN.username, ADMIN.password);
    await page.goto("/admin/futures-templates");
    await expect(page.locator("body")).toContainText("Шаблоны инструментов");

    const name = "e2e_tpl_" + crypto.randomUUID().replace(/-/g, "").slice(0, 6);
    await page.fill("#tpl-name", name);
    // бейджи недоступны без данных фьючерсов (MOEX отключён), submit-обработчик
    // перезаписывает hidden-поле — отправляем форму напрямую из браузера
    await page.evaluate(
      (n) =>
        fetch("/admin/futures-templates/save", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: "id=&name=" + encodeURIComponent(n) + "&tickers=W4V6%2CAFU6",
        }),
      name
    );
    await page.goto("/admin/futures-templates");
    const row = page.locator(`tr:has-text('${name}')`);
    await expect(row).toHaveCount(1);

    // удаление (форма с confirm-диалогом)
    page.on("dialog", (d) => d.accept());
    await Promise.all([
      page.waitForURL("**/admin/futures-templates*"),
      row.locator('form[action="/admin/futures-templates/delete"] button').click(),
    ]);
    await expect(page.locator(`tr:has-text('${name}')`)).toHaveCount(0);
  });

  test("admin: карточка update_oi с параметрами", async ({ page }) => {
    await login(page, ADMIN.username, ADMIN.password);
    await page.goto("/admin");
    const card = page.locator(
      'form[action="/admin/scripts/run"]:has(input[name="script"][value="update_oi"])'
    );
    await expect(card).toHaveCount(1);
    await expect(card.locator('input[list="futures-datalist"]')).toHaveCount(1);
    await expect(card.locator("#oi-all")).toHaveCount(1);
  });

  test("admin: блок «Реальное время» рендерится и сохраняет настройки", async ({ page }) => {
    await login(page, ADMIN.username, ADMIN.password);
    await page.goto("/admin");
    const form = page.locator('form[action="/admin/realtime/save"]');
    await expect(form).toHaveCount(1);
    // карточка демона присутствует в списке скриптов
    await expect(
      page.locator('form[action="/admin/scripts/run"]:has(input[name="script"][value="realtime_updater"])')
    ).toHaveCount(1);
    // включить актуализацию (чекбокс; скрытый input value=off для fallback одноимённый)
    const enabled = form.locator('input[type="checkbox"][name="realtime_enabled"]');
    if (!(await enabled.isChecked())) {
      await enabled.check();
    }
    await form.locator('input[name="interval_quotes_sec"]').fill("45");
    await Promise.all([
      page.waitForURL("**/admin"),
      form.locator('button[type="submit"]').click(),
    ]);
    // перезагружаем и проверяем состояние сохранилось
    await page.goto("/admin");
    const form2 = page.locator('form[action="/admin/realtime/save"]');
    await expect(
      form2.locator('input[type="checkbox"][name="realtime_enabled"]')
    ).toBeChecked();
    await expect(form2.locator('input[name="interval_quotes_sec"]')).toHaveValue("45");
  });
});
