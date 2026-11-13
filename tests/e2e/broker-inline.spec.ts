import {test, expect, Page} from '@playwright/test';
import {login, USER} from './helpers';

function fragment(environment: string, account = 'one', amount = '100', time = '12:00') {
  return `<div class="broker-portfolio-content" id="broker-content-${environment}" data-environment="${environment}" data-selected-account="${account}" aria-busy="false">
    <section id="broker-accounts-${environment}">${['one', 'two'].map(id => `<article class="broker-account"><h3>${id === 'one' ? 'Первый' : 'Второй'}</h3><form action="/broker/t-invest/select" method="post"><input type="hidden" name="environment" value="${environment}"><input type="hidden" name="account_id" value="${id}"><button class="btn" type="submit">Выбрать ${id}</button></form></article>`).join('')}</section>
    <section id="broker-portfolio-${environment}"><h3>Портфель ${account}</h3><p>Получено: ${time} UTC</p><p class="broker-total">Баланс: ${amount} rub</p><form action="/broker/t-invest/refresh" method="post"><input type="hidden" name="environment" value="${environment}"><button class="btn" type="submit">Обновить</button></form></section></div>`;
}

async function setup(page: Page, environment: string) {
  await page.route('**/v1/brokers/t-invest/sandbox/accounts', route => route.fulfill({json: {accounts: ['one', 'two'].map(id => ({id, name: id, masked_id: '••••' + id, status: 'ACCOUNT_STATUS_OPEN', rub_balance: '100'})), actions: [], error: null}}));
  await login(page, 'broker-production', USER.password);
  await page.goto('/settings/brokers');
  for (const env of ['production', 'sandbox']) {
    await page.locator('#broker-content-' + env).evaluate((node, html) => {node.outerHTML = html;}, fragment(env));
  }
  await page.locator(`[data-broker="${environment}"]`).click();
  return page.locator('#broker-workspace-' + environment);
}

test('Sandbox: поиск с клавиатуры, проверка той же заявки и продажа из позиции', async ({page}) => {
  const uid = '0df39455-d251-4ad2-b80f-5634c4280a11';
  let catalogCalls = 0;
  await page.route('**/accounts/one/instruments*', route => {
    catalogCalls++;
    expect(new URL(route.request().url()).search).toBe('');
    return route.fulfill({json: [
      {instrument_uid: uid, ticker: 'SBER', name: 'Сбербанк', class_code: 'TQBR', is_favorite: true},
      {instrument_uid: 'preferred', ticker: 'SBERP', name: 'Сбербанк ап', class_code: 'TQBR', is_favorite: false},
      {instrument_uid: 'gazprom', ticker: 'GAZP', name: 'Газпром', class_code: 'TQBR', is_favorite: true},
      {instrument_uid: 'sovcombank', ticker: 'SVCB', name: 'Совкомбанк', class_code: 'TQBR', is_favorite: false},
      ...Array.from({length: 20}, (_, index) => ({
        instrument_uid: 'other-' + index, ticker: 'XX' + index, name: 'Бумага ' + index,
        class_code: 'TQBR', is_favorite: false,
      })),
    ]});
  });
  const workspace = await setup(page, 'sandbox');
  const buy = '<div class="broker-heading"><h2>Портфель · Первый</h2><button type="button" data-stock-buy>Купить акцию</button></div>';
  const edit = '<button type="button" data-stock-name="Сбербанк" data-stock-edit="' + uid + '">Изменить</button>';
  await workspace.locator('#broker-portfolio-sandbox').evaluate((node, html) => node.insertAdjacentHTML('afterbegin', html), buy);
  await page.route('**/order-details', route => route.fulfill({json: {
    lot: 10, last_price: '250', available_shares: 10, available_lots: 1, buy_available: true, sell_available: true,
  }}));
  let key = '', orderCount = 0;
  await page.route('**/accounts/one/orders', async route => {
    const data = route.request().postDataJSON();
    expect(data.instrument_uid).toBe(uid);
    expect(data.lots).toBe(1);
    orderCount++;
    if (data.side === 'buy') {
      key = data.operation_key;
      return route.abort();
    }
    expect(data.side).toBe('sell');
    expect(data.operation_key).not.toBe(key);
    return route.fulfill({json: {status: 'filled', lots_requested: 1, lots_executed: 1}});
  });
  await page.route('**/accounts/one/orders/*', route => {
    expect(route.request().url()).toContain('/orders/' + key);
    return route.fulfill({json: {status: 'filled', lots_requested: 1, lots_executed: 1}});
  });
  await page.route('**/sandbox/portfolio-fragment', route => route.fulfill({contentType: 'text/html',
    body: fragment('sandbox', 'one', orderCount === 1 ? '7500' : '10000').replace('<h3>Портфель one</h3>', buy + (orderCount === 1 ? edit : '')),
  }));
  await workspace.getByRole('button', {name: 'Купить акцию'}).click();
  const dialog = page.getByRole('dialog');
  const search = dialog.getByRole('combobox', {name: 'Акция'});
  await expect(dialog.getByRole('listbox')).toBeVisible();
  await expect(dialog.getByRole('option')).toHaveText(['SBER · Сбербанк · TQBR', 'GAZP · Газпром · TQBR']);
  await search.fill('с');
  await expect(dialog.locator('[data-order-matches]')).toHaveText('Найдено: 3');
  await search.fill('сб');
  await expect(dialog.locator('[data-order-matches]')).toHaveText('Найдено: 2');
  await search.fill('SBERP');
  await expect(dialog.getByRole('option')).toHaveText(['SBERP · Сбербанк ап · TQBR']);
  await search.clear();
  await expect(dialog.getByRole('option')).toHaveText(['SBER · Сбербанк · TQBR', 'GAZP · Газпром · TQBR']);
  await search.fill('бумаг');
  const list = dialog.getByRole('listbox');
  await expect(list.getByRole('option')).toHaveCount(20);
  await list.hover();
  await page.mouse.wheel(0, 350);
  await expect.poll(() => list.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  await search.blur();
  await expect(dialog).toBeVisible();
  await expect(list).toBeVisible();
  await expect.poll(() => list.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  await dialog.getByLabel('Лотов').click();
  await expect(list).toBeHidden();
  await expect(dialog).toBeVisible();
  await search.fill('SBER');
  await expect(dialog.getByRole('option', {name: 'SBER · Сбербанк · TQBR'})).toBeVisible();
  await search.press('ArrowDown');
  await search.press('Enter');
  await dialog.getByRole('button', {name: 'Купить', exact: true}).click();
  await expect(dialog.getByRole('status')).toContainText('Статус неизвестен');
  await dialog.getByRole('button', {name: 'Закрыть заявку'}).click();
  await workspace.getByRole('button', {name: 'Проверить статус'}).click();
  await dialog.getByRole('button', {name: 'Проверить статус'}).click();
  await expect(dialog.getByRole('status')).toContainText('Исполнена');
  await dialog.getByRole('button', {name: 'Закрыть заявку'}).click();
  await expect(workspace.locator('.broker-total')).toHaveText('Баланс: 7500 rub');
  await workspace.getByRole('button', {name: 'Изменить'}).click();
  await expect(dialog).toContainText('Доступно к продаже: 10 акций · 1 лотов');
  await dialog.getByLabel('Лотов').fill('2');
  await expect(dialog.getByRole('button', {name: 'Продать', exact: true})).toBeDisabled();
  await dialog.getByLabel('Лотов').fill('1');
  await dialog.getByRole('button', {name: 'Продать', exact: true}).click();
  await expect(dialog.getByRole('status')).toContainText('Исполнена');
  await expect(workspace.locator('.broker-total')).toHaveText('Баланс: 10000 rub');
  expect(orderCount).toBe(2);
  expect(catalogCalls).toBe(1);
});

test('Sandbox: черновик и переключатель MCP, неизвестный статус без сверки', async ({page}) => {
  const workspace = await setup(page, 'sandbox');
  const uid = '0df39455-d251-4ad2-b80f-5634c4280a11';
  await workspace.locator('#broker-portfolio-sandbox').evaluate(node => {
    node.insertAdjacentHTML('afterbegin', '<div class="broker-heading"><h2>Портфель · Первый</h2></div>');
  });
  await page.route('**/accounts/one/instruments', route => route.fulfill({json: []}));
  await page.route('**/order-details', route => route.fulfill({json: {
    lot: 10, last_price: '250', available_rub: '10000', connection_version: 7,
    available_shares: 10, available_lots: 1, buy_available: true, sell_available: true,
  }}));
  let calls = 0;
  await page.route('**/accounts/one/orders', route => {
    calls++;
    expect(route.request().postDataJSON()).toMatchObject({
      transport: 'mcp', instrument_uid: uid, side: 'buy', lots: 2, connection_version: 7, last_price: '250',
    });
    return route.fulfill({json: {transport: 'mcp', status: 'unknown', can_check: false, lots_requested: 2}});
  });
  await page.route('**/mcp/models?*', route => route.fulfill({json: {
    provider: 'deepseek', configured: true, default_model: 'demo', models: ['demo'], warning: null,
  }}));
  await page.route('**/mcp/check', route => route.fulfill({json: {
    available: true, environment: 'sandbox', checked_at: '2026-11-13T12:00:00Z', capabilities: [],
    tools: [{name: 'invest_get_positions', approved: true, schema_sha256: 'a'.repeat(64)}],
  }}));
  await page.route('**/mcp/check-llm', route => route.fulfill({json: {
    ...route.request().postDataJSON(), tool_calling: true, expires_at: Math.floor(Date.now() / 1000) + 900, check_token: 'test-proof',
  }}));
  await page.route('**/mcp/ask', route => route.fulfill({json: {
    ...route.request().postDataJSON(), status: 'ok', answer: 'Черновик заявки готов.',
    evidence: [], warnings: [], calculations: [],
    order_draft: {instrument_uid: uid, ticker: 'SBER', name: 'Сбербанк', side: 'buy', lots: 2},
  }}));
  const mcpTab = workspace.getByRole('tab', {name: 'MCP-аналитика'});
  await mcpTab.click();
  await workspace.locator('[data-mcp-check]').click();
  await workspace.locator('[data-mcp-check-llm]').click();
  await workspace.locator('[data-mcp-question]').fill('Купи два лота SBER');
  await workspace.locator('[data-mcp-ask]').click();
  const openDraft = async () => {
    await mcpTab.click();
    await workspace.getByRole('button', {name: 'Проверить заявку'}).click();
  };
  const dialog = page.getByRole('dialog');
  await openDraft();
  const transport = dialog.getByRole('switch');
  await expect(transport).toHaveAttribute('aria-checked', 'false');
  await transport.click();
  await dialog.getByRole('button', {name: 'Закрыть заявку'}).click();
  await openDraft();
  await expect(transport).toHaveAttribute('aria-checked', 'true');
  await expect(dialog.getByLabel('Лотов')).toHaveValue('2');
  await dialog.getByRole('button', {name: 'Купить', exact: true}).click();
  await expect(dialog.getByRole('status')).toContainText('Статус неизвестен');
  await expect(dialog.getByRole('button', {name: 'Проверить статус'})).toBeHidden();
  expect(calls).toBe(1);
});

for (const environment of ['production', 'sandbox']) {
  test(`${environment}: выбор и повторные обновления без навигации`, async ({page}) => {
    const workspace = await setup(page, environment);
    if (environment === 'sandbox') {
      await expect(page.locator('#sandbox-accounts .sandbox-account')).toHaveCount(2);
      await expect(page.locator('#sandbox-accounts [name="operation_key"]')).toHaveCount(4);
    }
    const keys = environment === 'sandbox' ? await page.locator('#sandbox-accounts [name="operation_key"]').evaluateAll(nodes => nodes.map(node => (node as HTMLInputElement).value)) : [];
    const url = page.url();
    let navigations = 0, calls = 0;
    page.on('framenavigated', frame => {if (frame === page.mainFrame()) navigations++;});
    await page.route(/\/broker\/t-invest\/(select|refresh)$/, async route => {
      expect(route.request().headers()['x-requested-with']).toBe('XMLHttpRequest');
      const raw = route.request().postData() || '';
      expect(raw).toContain(environment);
      calls++;
      await route.fulfill({contentType: 'text/html', body: fragment(environment, 'two', String(100 + calls), '12:01')});
    });
    await workspace.getByRole('button', {name: 'Выбрать two', exact: true}).click();
    await expect(workspace.locator('.broker-total')).toHaveText('Баланс: 101 rub');
    await workspace.getByRole('button', {name: 'Обновить', exact: true}).click();
    await expect(workspace.locator('.broker-total')).toHaveText('Баланс: 102 rub');
    await workspace.getByRole('button', {name: 'Обновить', exact: true}).click();
    await expect(workspace.locator('.broker-total')).toHaveText('Баланс: 103 rub');
    await expect(workspace).toContainText('Получено: 12:01 UTC');
    expect(calls).toBe(3); expect(navigations).toBe(0); expect(page.url()).toBe(url);
    await expect(page.locator(`[data-broker="${environment}"]`)).toHaveAttribute('aria-pressed', 'true');
    if (environment === 'sandbox') {
      await expect(page.locator('.sandbox-account:visible .sandbox-name')).toHaveText('two');
      expect(await page.locator('#sandbox-accounts [name="operation_key"]').evaluateAll(nodes => nodes.map(node => (node as HTMLInputElement).value))).toEqual(keys);
    }
    await page.setViewportSize({width: 390, height: 844});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  });
}

test('Загрузка блокирует повторный запрос, поздний ответ остаётся в своём контуре', async ({page}) => {
  const production = await setup(page, 'production');
  let calls = 0;
  let release!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/broker/t-invest/select', async route => {
    calls++;
    await gate;
    await route.fulfill({contentType: 'text/html', body: fragment('production', 'two', '777')});
  });
  await production.getByRole('button', {name: 'Выбрать two', exact: true}).click();
  await expect(production.locator('[aria-busy="true"]')).toHaveCount(1);
  await expect(production.getByRole('status')).toContainText('Обновляется… Счёт: Второй');
  await expect(production.getByRole('button', {name: 'Выбрать one', exact: true})).toBeDisabled();
  await production.locator('form').filter({has: page.locator('[value="two"]')}).evaluate(form => (form as HTMLFormElement).requestSubmit());
  await expect.poll(() => calls).toBe(1);
  await page.locator('[data-broker="sandbox"]').click();
  const sandbox = page.locator('#broker-workspace-sandbox');
  await expect(sandbox).toBeVisible();
  release();
  await expect(production.locator('.broker-total')).toHaveText('Баланс: 777 rub');
  await expect(production).toBeHidden();
  await expect(sandbox.locator('.broker-total')).toHaveText('Баланс: 100 rub');
  await page.locator('[data-broker="production"]').click();
  await expect(production.getByRole('button', {name: 'Обновить', exact: true})).toBeEnabled();
});

for (const failure of ['network', '401', '409', '503', 'json', 'partial', 'wrong-account', 'login-html']) {
  test(`Ошибка ${failure}: прежние данные сохранены, повторное обновление возможно`, async ({page}) => {
    const workspace = await setup(page, 'production');
    let calls = 0;
    await page.route('**/broker/t-invest/refresh', async route => {
      if (++calls > 1) return route.fulfill({contentType: 'text/html', body: fragment('production', 'one', '999', '12:05')});
      if (failure === 'network') return route.abort();
      if (['401', '409', '503'].includes(failure)) return route.fulfill({status: Number(failure), json: {detail: failure === '401' ? 'Сессия истекла. Войдите в приложение.' : 'Повторите обновление позже.'}});
      if (failure === 'json') return route.fulfill({json: {result: 'unexpected'}});
      const body = failure === 'partial' ? '<p>Неполный ответ</p>' : failure === 'login-html' ? '<!doctype html><html><body>Вход</body></html>' : fragment('production', 'two', '0');
      return route.fulfill({contentType: 'text/html', body});
    });
    await workspace.getByRole('button', {name: 'Обновить', exact: true}).click();
    await expect(workspace.locator('.broker-update-error')).toBeVisible();
    await expect(workspace.locator('.broker-total')).toHaveText('Баланс: 100 rub');
    await expect(workspace).toContainText('Получено: 12:00 UTC');
    await expect(workspace.locator('.broker-portfolio-content')).toHaveAttribute('aria-busy', 'false');
    await workspace.getByRole('button', {name: 'Обновить', exact: true}).click();
    await expect(workspace.locator('.broker-total')).toHaveText('Баланс: 999 rub');
    await expect(workspace.locator('.broker-update-error')).toBeHidden();
    expect(calls).toBe(2);
  });
}
