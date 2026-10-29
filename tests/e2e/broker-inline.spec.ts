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
