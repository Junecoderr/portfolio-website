import { test, expect } from '@playwright/test';

const noConsoleErrors = (page) => {
  const errors = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });
  page.on('pageerror', (e) => errors.push(String(e)));
  return errors;
};

test('home page renders prerendered content and hydrates cleanly', async ({ page }) => {
  const errors = noConsoleErrors(page);
  await page.goto('/');
  await expect(page).toHaveTitle(/Tanisha Brahma/);
  await expect(page.locator('h1')).toHaveCount(1);
  await expect(page.locator('script[type="application/ld+json"]')).toHaveCount(1);
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/$/);
  await page.waitForTimeout(2500);
  expect(errors).toEqual([]);
});

test('case study page opens its dialog and has its own metadata', async ({ page }) => {
  const errors = noConsoleErrors(page);
  await page.goto('/work/blackout.html');
  await expect(page).toHaveTitle(/Blackout/);
  await expect(page.locator('[role="dialog"]')).toBeVisible();
  await expect(page.locator('#case-title')).toHaveText('Blackout');
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/work\/blackout$/);
  await page.keyboard.press('Escape');
  await expect(page.locator('[role="dialog"]')).toHaveCount(0);
  await page.waitForTimeout(1500);
  expect(errors).toEqual([]);
});

test('opening a case from the grid updates the URL and back closes it', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(1600);
  await page.locator('.work-card').first().locator('.work-cta').click();
  await expect(page.locator('[role="dialog"]')).toBeVisible();
  await expect(page).toHaveURL(/\/work\/sentinel$/);
  await page.goBack();
  await expect(page.locator('[role="dialog"]')).toHaveCount(0);
});

test('sitemap and robots are served', async ({ request }) => {
  const sitemap = await request.get('/sitemap.xml');
  expect(sitemap.ok()).toBeTruthy();
  expect(await sitemap.text()).toContain('/work/blackout');
  const robots = await request.get('/robots.txt');
  expect(await robots.text()).toContain('Sitemap:');
});
