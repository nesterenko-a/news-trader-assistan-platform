import type { Page } from "@playwright/test";

export const ADMIN = { username: "admin", password: "admin123" };
export const USER = { username: "user", password: "user123" };

export async function login(
  page: Page,
  username: string,
  password: string
): Promise<void> {
  await page.goto("/login");
  await page.fill('input[name="username"]', username);
  await page.fill('input[name="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForLoadState("domcontentloaded");
}

export async function register(
  page: Page,
  username: string,
  password: string
): Promise<void> {
  await page.goto("/register");
  await page.fill('input[name="username"]', username);
  await page.fill('input[name="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForLoadState("domcontentloaded");
}

export async function hasToken(page: Page): Promise<boolean> {
  const cookies = await page.context().cookies();
  return cookies.some((c) => c.name === "nt_token");
}
