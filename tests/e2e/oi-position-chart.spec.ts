import { expect, test } from "@playwright/test";

test("График OI переключает группу и период и показывает значения точки", async ({ page }) => {
  await page.goto("/indicators?name=oi&ticker=SBER");
  const chart = page.locator("#oi-position-chart");
  await expect(chart).toBeVisible();
  await expect(chart).toContainText("Выше нуля — физлица в лонге");
  await expect(chart.locator(".oi-net-line")).toHaveCount(1);
  await expect(chart.locator(".oi-net-area.positive").first()).toBeVisible();
  await expect(chart).toContainText("Лиц в шорте");

  await chart.locator(".oi-net-hit").last().focus();
  await expect(chart.locator("#oi-position-details")).toContainText("Нетто");

  await chart.getByRole("link", { name: "Юридические лица" }).click();
  await expect(page.locator("#oi-position-chart")).toContainText("ниже нуля — юрлица в шорте");
  await expect(page.locator("#oi-position-chart .oi-net-area.negative").first()).toBeVisible();
  await page.locator('#oi-position-chart nav[aria-label="Период графика позиций"]').getByRole("link", { name: "Всё" }).click();
  await expect(page).toHaveURL(/cg_period=all/);
  await expect(page).toHaveURL(/cg_group=juridical/);
  await page.setViewportSize({ width: 375, height: 760 });
  await expect(page.locator("#oi-position-chart")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});
