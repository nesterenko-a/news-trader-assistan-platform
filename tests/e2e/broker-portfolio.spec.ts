import { test, expect } from "@playwright/test";
import { register, login, USER } from "./helpers";

test("Т-Инвестиции: доступ к подключению и пустому портфелю", async ({ page }) => {
  const name = "broker_" + crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  await register(page, name, "secret123");
  await page.goto("/settings#brokers");
  await expect(page.locator("#brokers")).toContainText("Т‑Инвестиции");
  await expect(page.locator("#brokers")).toContainText("только для чтения");
  await page.goto("/portfolio/brokers/t-invest");
  await expect(page.locator("main")).toContainText("Счёт ещё не подключён");
  await page.goto("/portfolio");
  await expect(page.locator('a[href="/portfolio/brokers/t-invest"]')).toBeVisible();
});

test("Песочница: контур сохранён, пустые счета объяснены, токен не раскрыт", async ({ page }) => {
  await login(page, USER.username, USER.password);
  await page.goto("/settings#brokers");
  const brokers = page.locator("#brokers");
  await expect(brokers).toContainText("Подключено: Песочница");
  await expect(brokers).toContainText("В песочнице пока нет счетов");
  await expect(brokers.locator('select[name="environment"]')).toHaveValue("sandbox");
  await brokers.locator('select[name="environment"]').selectOption("production");
  await expect(brokers.locator('select[name="environment"]')).toHaveValue("production");
  await expect(brokers.locator('input[name="token"]')).toHaveValue("");
  await page.reload();
  await expect(brokers.locator('select[name="environment"]')).toHaveValue("sandbox");
  await page.goto("/settings?broker_error=422&environment=production#brokers");
  await expect(brokers.locator('select[name="environment"]')).toHaveValue("production");
  await page.goto("/portfolio/brokers/t-invest");
  await expect(page.locator("main")).toContainText("Песочница · тестовые средства");
  await expect(page.locator("main")).toContainText("В песочнице пока нет счетов");
});

test("Обычный пользователь видит восстановление сертификата после ошибки 502", async ({ page }) => {
  const name = "broker_" + crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  await register(page, name, "secret123");
  await page.goto("/settings?broker_error=502#brokers");
  await expect(page.locator("#brokers")).toContainText("Проверьте сертификат");
  await expect(page.locator('#brokers form[action="/broker/t-invest/repair-certificate"] button')).toBeVisible();
});
