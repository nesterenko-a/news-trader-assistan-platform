import { test, expect } from "@playwright/test";
import { login, USER, register } from "./helpers";

test("песочница: создать, пополнить, отменить и подтвердить закрытие", async ({ page }) => {
  let accounts: any[] = [];
  let actions: any[] = [];
  const calls: string[] = [];
  await page.route("**/v1/brokers/t-invest/sandbox/accounts", route => route.fulfill({json: {accounts, actions, error: null}}));
  await page.route("**/broker/t-invest/sandbox/*", async route => {
    if (route.request().method() !== "POST") return route.continue();
    const kind = route.request().url().split("/").pop()!;
    const body = new URLSearchParams(route.request().postData() || "");
    calls.push(kind);
    if (kind === "create") {
      expect(body.get("operation_key")).toMatch(/^[0-9a-f-]{36}$/);
      accounts = [{id: "sandbox-account", name: body.get("name"), masked_id: "••••1234", status: "Открыт", rub_balance: "0"}];
    }
    if (kind === "pay-in") {
      expect(body.get("account_id")).toBe("sandbox-account");
      expect(body.get("amount")).toBe("1000,50");
      accounts[0].rub_balance = "1000.50";
    }
    if (kind === "close") {
      expect(body.get("confirmed")).toBe("true");
      accounts = [];
    }
    actions = [{kind: kind.replace("-", "_"), message: "Действие выполнено", error_code: null}];
    await route.fulfill({status: 303, headers: {location: "/broker/t-invest/sandbox"}});
  });
  await login(page, USER.username, USER.password);
  await page.goto("/settings#brokers");
  await page.locator('#brokers a[href="/broker/t-invest/sandbox"]').click();
  await expect(page.getByRole("heading", {name: "Песочница Т‑Инвестиций"})).toBeVisible();
  await expect(page.locator("#sandbox-empty")).toBeVisible();
  await page.locator('#sandbox-create input[name="name"]').fill("Учебный");
  await page.locator("#sandbox-create").getByRole("button", {name: "Создать", exact: true}).click();
  const account = page.locator(".sandbox-account");
  await expect(account).toHaveCount(1);
  await expect(account).toContainText("Учебный");
  await account.locator('[name="amount"]').fill("1000,50");
  await account.getByRole("button", {name: "Пополнить"}).click();
  await expect(account).toContainText("1000.50 ₽");
  await account.locator('[name="confirmed"]').check();
  page.once("dialog", dialog => dialog.dismiss());
  await account.getByRole("button", {name: "Закрыть счёт"}).click();
  await expect(account).toHaveCount(1);
  expect(calls).toEqual(["create", "pay-in"]);
  page.once("dialog", dialog => dialog.accept());
  await account.getByRole("button", {name: "Закрыть счёт"}).click();
  await expect(page.locator("#sandbox-empty")).toBeVisible();
  expect(calls).toEqual(["create", "pay-in", "close"]);
  await page.reload();
  expect(calls).toHaveLength(3);
});

test("песочница: вход, отсутствие подключения и мобильная ошибка", async ({ page }) => {
  await page.goto("/broker/t-invest/sandbox");
  await expect(page).toHaveURL(/\/login/);
  await register(page, "sb_" + crypto.randomUUID().slice(0, 8), "secret123");
  await page.goto("/broker/t-invest/sandbox");
  await expect(page.getByRole("link", {name: "Подключить песочницу"})).toBeVisible();
  await expect(page.locator("#sandbox-create")).toHaveCount(0);
  await login(page, USER.username, USER.password);
  await page.route("**/v1/brokers/t-invest/sandbox/accounts", route => route.fulfill({json: {accounts: [], actions: [{kind: "pay_in", message: "Результат неизвестен. Проверьте баланс", error_code: "transport_unknown"}], error: "Не удалось получить счета"}}));
  await page.setViewportSize({width: 390, height: 844});
  await page.goto("/broker/t-invest/sandbox");
  await expect(page.locator("#sandbox-error")).toContainText("Не удалось получить счета");
  await expect(page.locator("#sandbox-actions")).toContainText("Результат неизвестен");
  await expect(page.locator("#sandbox-empty")).toBeHidden();
});
