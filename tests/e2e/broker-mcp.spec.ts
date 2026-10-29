import {test, expect} from '@playwright/test';
import fs from 'fs';
import {login, USER} from './helpers';

test.beforeEach(async ({page}) => {
  await page.route('**/v1/brokers/t-invest/mcp/models?*', route => {
    const provider = new URL(route.request().url()).searchParams.get('provider');
    return route.fulfill({json: {provider, configured: true, default_model: provider + '-configured', models: [provider + '-configured', provider + '-other'], warning: null}});
  });
});

test('MCP: экспорт схем скачивает JSON выбранного контура без вызова LLM', async ({page}) => {
  let llmCalls = 0;
  await page.route('**/v1/brokers/t-invest/mcp/check-llm', route => {llmCalls++; return route.abort();});
  await page.route('**/v1/brokers/t-invest/mcp/check', route => {
    expect(route.request().postDataJSON()).toEqual({environment: 'production', include_schemas: true});
    return route.fulfill({json: {available: true, environment: 'production', checked_at: '2026-10-30T12:00:00Z', capabilities: [], catalog_format: 'newstrader-mcp-review-v1', tools: [{name: 'test_read', approved: false, schema_sha256: 'a'.repeat(64), input_schema: {type: 'object', properties: {}}, omitted_keywords: ['description']}]}});
  });
  await login(page, 'broker-production', USER.password);
  await page.goto('/settings/brokers');
  await page.locator('[data-broker="production"]').click();
  const workspace = page.locator('#broker-workspace-production');
  await workspace.getByRole('tab', {name: 'MCP-аналитика'}).click();
  const downloading = page.waitForEvent('download');
  await workspace.getByRole('button', {name: 'Скачать схемы MCP'}).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe('t-invest-mcp-catalog-production.json');
  const file = await download.path();
  const data = JSON.parse(fs.readFileSync(file!, 'utf8'));
  expect(data.tools[0].input_schema).toEqual({type: 'object', properties: {}});
  expect(data.tools[0].schema_sha256).toHaveLength(64);
  expect(llmCalls).toBe(0);
  await expect(workspace.locator('[data-mcp-status]')).toContainText('подготовлен для скачивания');
  await expect(workspace.getByRole('button', {name: 'Скачать схемы MCP'})).toBeEnabled();
});

test('LLM: явная проверка, смена пары и отсутствие скрытого переключения', async ({page}) => {
  const checks: object[] = [];
  await page.route('**/v1/brokers/t-invest/mcp/check-llm', async route => {
    const pair = route.request().postDataJSON(); checks.push(pair);
    if (pair.model.endsWith('other')) return route.fulfill({status: 502, json: {detail: 'Модель не поддерживает инструменты'}});
    return route.fulfill({json: {...pair, tool_calling: true, expires_at: Math.floor(Date.now() / 1000) + 900, check_token: 'test-proof'}});
  });
  await page.route('**/v1/brokers/t-invest/sandbox/accounts', route => route.fulfill({json: {accounts: [], actions: [], error: null}}));
  await login(page, 'broker-production', USER.password);
  await page.goto('/settings/brokers');
  await page.locator('[data-broker="production"]').click();
  const workspace = page.locator('#broker-workspace-production');
  await workspace.getByRole('tab', {name: 'MCP-аналитика'}).click();
  const check = workspace.locator('[data-mcp-check-llm]'), status = workspace.locator('[data-mcp-llm-status]');
  await expect(check).toBeEnabled();
  expect(checks).toEqual([]);
  await check.click();
  await expect(status).toContainText('Вызов инструментов проверен: deepseek / deepseek-configured');
  await workspace.locator('[data-mcp-model]').selectOption('deepseek-other');
  await expect(status).toContainText('Проверьте LLM повторно');
  await check.click();
  await expect(status).toHaveText('Модель не поддерживает инструменты');
  await expect(workspace.locator('[data-mcp-provider]')).toHaveValue('deepseek');
  await workspace.locator('[data-mcp-provider]').selectOption('chatgpt');
  await expect(workspace.locator('[data-mcp-model]')).toHaveValue('chatgpt-configured');
  await check.click();
  await expect(status).toContainText('Вызов инструментов проверен: chatgpt / chatgpt-configured');
  expect(checks).toHaveLength(3);
});

test('LLM: недоступный каталог и ключ обозначены явно', async ({page}) => {
  await page.route('**/v1/brokers/t-invest/mcp/models?*', route => route.fulfill({json: {provider: 'deepseek', configured: false, default_model: 'configured', models: ['configured'], warning: 'Ключ провайдера не настроен'}}));
  await login(page, 'broker-production', USER.password);
  await page.goto('/settings/brokers');
  await page.locator('[data-broker="production"]').click();
  const workspace = page.locator('#broker-workspace-production');
  await workspace.getByRole('tab', {name: 'MCP-аналитика'}).click();
  await expect(workspace.locator('[data-mcp-llm-status]')).toHaveText('Ключ провайдера не настроен');
  await expect(workspace.locator('[data-mcp-check-llm]')).toBeDisabled();
});

for (const environment of ['production', 'sandbox']) {
  test(`MCP: диагностика ${environment}, ошибки и возврат к портфелю`, async ({page}) => {
    let calls = 0;
    await page.route('**/v1/brokers/t-invest/sandbox/accounts', route => route.fulfill({json: {accounts: [], actions: [], error: null}}));
    await page.route('**/v1/brokers/t-invest/mcp/check', async route => {
      expect(route.request().postDataJSON()).toEqual({environment});
      calls++;
      if (calls === 2) return route.fulfill({status: 502, json: {detail: 'MCP не принял токен'}});
      return route.fulfill({json: {available: true, environment, checked_at: '2026-10-30T12:00:00Z', capabilities: [{name: 'Портфель', status: 'unavailable', reason: 'Адаптер ещё не проверен'}], tools: [{name: '<img src=x onerror=alert(1)>', schema_sha256: 'test-schema-digest', approved: false}]}});
    });
    await login(page, 'broker-production', USER.password);
    await page.goto('/settings/brokers');
    await page.locator(`[data-broker="${environment}"]`).click();
    const workspace = page.locator('#broker-workspace-' + environment);
    expect(calls).toBe(0);
    await workspace.getByRole('tab', {name: 'MCP-аналитика'}).click();
    await expect(workspace.locator('[data-broker-view="portfolio"]')).toBeHidden();
    await workspace.locator('[data-mcp-check]').click();
    await expect(workspace.locator('[data-mcp-status]')).toContainText('MCP доступен');
    await workspace.locator('summary').filter({hasText: 'Возможности и источники'}).click();
    await expect(workspace.locator('[data-mcp-tools]')).toContainText('<img src=x');
    await expect(workspace.locator('[data-mcp-tools] img')).toHaveCount(0);
    await workspace.locator('[data-mcp-check]').click();
    await expect(workspace.locator('[data-mcp-error]')).toHaveText('MCP не принял токен');
    await expect(workspace.locator('[data-mcp-details]')).toBeHidden();
    await expect(workspace.locator('[data-mcp-check]')).toBeEnabled();
    await page.setViewportSize({width: 375, height: 667});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    await workspace.getByRole('tab', {name: 'Портфель', exact: true}).click();
    await expect(workspace.locator('[data-broker-view="portfolio"]')).toBeVisible();
  });
}
