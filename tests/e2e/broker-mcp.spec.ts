import {test, expect} from '@playwright/test';
import {login, USER} from './helpers';

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
