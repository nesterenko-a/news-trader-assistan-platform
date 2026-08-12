import { test, expect } from "@playwright/test";
import { USER, hasToken } from "./helpers";

test.describe("Авторизация", () => {
  test("неверные данные — ошибка и без cookie", async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[name="username"]', "nosuchuser");
    await page.fill('input[name="password"]', "wrongpass");
    await page.click('button[type="submit"]');
    await page.waitForSelector("p.error");
    await expect(page.locator("p.error")).toContainText(
      "Неверное имя пользователя или пароль"
    );
    expect(await hasToken(page)).toBe(false);
  });

  test("валидный вход ставит cookie", async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[name="username"]', USER.username);
    await page.fill('input[name="password"]', USER.password);
    await page.click('button[type="submit"]');
    await page.waitForURL("/");
    expect(await hasToken(page)).toBe(true);
  });

  test("короткие значения в регистрации блокируются", async ({ page }) => {
    await page.goto("/register");
    await page.fill('input[name="username"]', "ab");
    await page.fill('input[name="password"]', "123");
    await page.click('button[type="submit"]');
    // HTML5-валидация (minlength 3/6) блокирует отправку формы
    expect(page.url()).toContain("/register");
    expect(await hasToken(page)).toBe(false);
  });

  test("дубликат пользователя — ошибка", async ({ page }) => {
    await page.goto("/register");
    await page.fill('input[name="username"]', USER.username);
    await page.fill('input[name="password"]', USER.password);
    await page.click('button[type="submit"]');
    await page.waitForSelector("p.error");
    await expect(page.locator("p.error")).toContainText(
      "Пользователь уже существует"
    );
  });

  test("полный цикл: регистрация → вход → выход", async ({ page }) => {
    const name = "user_" + crypto.randomUUID().replace(/-/g, "").slice(0, 8);
    await page.goto("/register");
    await page.fill('input[name="username"]', name);
    await page.fill('input[name="password"]', "secret123");
    await page.click('button[type="submit"]');
    await page.waitForURL("/");
    expect(await hasToken(page)).toBe(true);

    // приватная страница доступна
    await page.goto("/watchlist");
    await expect(page.locator("h1")).toHaveText("Watchlist");

    // выход удаляет сессию
    await page.goto("/logout");
    expect(await hasToken(page)).toBe(false);

    // после выхода приватная страница требует авторизации
    await page.goto("/watchlist");
    expect(page.url()).toContain("/login");
  });
});
