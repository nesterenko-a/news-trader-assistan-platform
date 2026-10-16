import { test, expect } from "@playwright/test";
import { register, login, USER } from "./helpers";

test("Брокеры вынесены в отдельное тело с общей шапкой и навигацией настроек", async ({page}) => {
  await login(page, USER.username, USER.password);
  await page.goto("/settings");
  const heading = await page.locator('.settings-head').innerText();
  const links = await page.locator('.settings-nav a').evaluateAll(items => items.map(item => [item.textContent, item.getAttribute('href')]));
  await expect(page.locator('#brokers')).toHaveCount(0);
  await page.locator('.settings-nav').getByRole('link', {name: 'Брокеры и торговля'}).click();
  await expect(page).toHaveURL(/\/settings\/brokers$/);
  await expect(page.locator('.settings-head')).toHaveText(heading);
  expect(await page.locator('.settings-nav a').evaluateAll(items => items.map(item => [item.textContent, item.getAttribute('href')]))).toEqual(links);
  await expect(page.locator('.settings-nav [aria-current="page"]')).toHaveText('Брокеры и торговля');
  await expect(page.locator('.settings-content > section')).toHaveCount(4);
  await expect(page.locator('#brokers')).toBeVisible();
  await expect(page.locator('#profile')).toHaveCount(0);
  await page.screenshot({path: 'test-results/broker-settings-desktop.png', fullPage: true});
  await page.locator('.settings-nav').getByRole('link', {name: 'Профиль', exact: true}).click();
  await expect(page).toHaveURL(/\/settings#profile$/);
  await expect(page.locator('#profile')).toBeVisible();
  await page.goto('/settings?broker_error=422&environment=sandbox#brokers');
  await expect(page).toHaveURL(/\/settings\/brokers\?broker_error=422&environment=sandbox/);
  await expect(page.locator('#brokers [name="environment"]')).toHaveValue('sandbox');
  await page.goto('/settings#brokers');
  await expect(page).toHaveURL(/\/settings\/brokers$/);
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.locator('#brokers')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
});

test("Т-Инвестиции: доступ к подключению и пустому портфелю", async ({ page }) => {
  const name = "broker_" + crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  await register(page, name, "secret123");
  await page.goto("/settings/brokers");
  await expect(page.locator("#brokers")).toContainText("Т‑Инвестиции");
  await expect(page.locator("#brokers")).toContainText("только для чтения");
  for (const id of ['broker-portfolio', 'broker-accounts', 'broker-sandbox']) {
    await expect(page.locator('#' + id)).toHaveAttribute('aria-disabled', 'true');
    await expect(page.locator('#' + id)).toHaveClass(/broker-panel-disabled/);
    await expect(page.locator('#' + id + ' form')).toHaveCount(0);
  }
  await expect(page.locator('script[src="/static/broker-sandbox.js"]')).toHaveCount(0);
  await page.goto("/portfolio/brokers/t-invest");
  await expect(page).toHaveURL(/settings\/brokers#broker-portfolio$/);
  await expect(page.locator("#broker-portfolio")).toContainText("Счёт ещё не подключён");
  await page.goto("/portfolio");
  await expect(page.locator('a[href="/portfolio/brokers/t-invest"]')).toBeVisible();
});

test("Песочница: контур сохранён, пустые счета объяснены, токен не раскрыт", async ({ page }) => {
  await login(page, USER.username, USER.password);
  await page.goto("/settings/brokers");
  const brokers = page.locator("#brokers");
  await expect(brokers).toContainText("Подключено: Песочница");
  await expect(brokers).toContainText("В песочнице пока нет счетов");
  await expect(brokers.locator('select[name="environment"]')).toHaveValue("sandbox");
  await brokers.locator('select[name="environment"]').selectOption("production");
  await expect(brokers.locator('select[name="environment"]')).toHaveValue("production");
  await expect(brokers.locator('input[name="token"]')).toHaveValue("");
  await page.reload();
  await expect(brokers.locator('select[name="environment"]')).toHaveValue("sandbox");
  await page.goto("/settings/brokers?broker_error=422&environment=production");
  await expect(brokers.locator('select[name="environment"]')).toHaveValue("production");
  await page.locator('#brokers a[href="#broker-portfolio"]').click();
  await expect(page).toHaveURL(/settings\/brokers\?broker_error=422&environment=production#broker-portfolio$/);
  await expect(page.locator('#broker-portfolio')).toHaveAttribute('aria-disabled', 'false');
  await expect(page.locator('#broker-sandbox')).toHaveAttribute('aria-disabled', 'false');
  await expect(page.locator("main")).toContainText("Песочница · тестовые средства");
  await expect(page.locator("main")).toContainText("В песочнице пока нет счетов");
});

test("Обычный пользователь видит восстановление сертификата после ошибки 502", async ({ page }) => {
  const name = "broker_" + crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  await register(page, name, "secret123");
  await page.goto("/settings/brokers?broker_error=502");
  await expect(page.locator("#brokers")).toContainText("Проверьте сертификат");
  await expect(page.locator('#brokers form[action="/broker/t-invest/repair-certificate"] button')).toBeVisible();
});


test("Рабочий контур: управление счетами, баланс и загрузка портфеля", async ({page}) => {
  const calls: string[] = [];
  await page.route('**/v1/brokers/t-invest/sandbox/accounts', route => {
    calls.push('sandbox');
    return route.abort();
  });
  await login(page, 'broker-production', USER.password);
  await page.goto('/settings/brokers');
  await page.locator('#brokers').getByRole('link', {name: 'Управление рабочими счетами'}).click();
  await expect(page).toHaveURL(/settings\/brokers#broker-accounts$/);
  const panel = page.locator('#broker-accounts');
  await expect(panel).toHaveAttribute('aria-disabled', 'false');
  await expect(panel.locator('.broker-account')).toHaveCount(2);
  const first = panel.locator('.broker-account').filter({has: page.getByRole('heading', {name: 'Основной', exact: true})});
  await expect(first).toContainText('••••1234');
  await expect(first).toContainText('Выбран для портфеля');
  await expect(first).toContainText('1500.50 rub');
  await expect(first).toContainText('Получено:');
  const second = panel.locator('.broker-account').filter({has: page.getByRole('heading', {name: 'Второй', exact: true})});
  await expect(second).toContainText('Баланс ещё не загружен');
  await expect(page.locator('#broker-sandbox')).toHaveAttribute('aria-disabled', 'true');
  await expect(page.locator('#sandbox-create')).toHaveCount(0);
  await page.route('**/broker/t-invest/select', async route => {
    const body = new URLSearchParams(route.request().postData() || '');
    calls.push(body.get('account_id')!);
    await route.fulfill({status: 303, headers: {location: '/settings/brokers#broker-portfolio'}});
  });
  await first.getByRole('button', {name: 'Обновить портфель', exact: true}).click();
  await expect(page).toHaveURL(/settings\/brokers#broker-portfolio$/);
  await second.getByRole('button', {name: 'Выбрать и загрузить портфель', exact: true}).click();
  await expect(page).toHaveURL(/settings\/brokers#broker-portfolio$/);
  expect(calls).toEqual(['production-1234', 'production-5678']);
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/settings/brokers#broker-accounts');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.screenshot({path: 'test-results/broker-production-mobile.png', fullPage: true});
});
