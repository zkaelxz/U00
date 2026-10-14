import { expect, test } from '@playwright/test'

import { openSettingsGroups } from './settingsNav'

test('Settings > Display sets the page width and text size, and they survive a reload', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Sharing and devices')
  const html = page.locator('html')
  await expect(html).not.toHaveAttribute('data-width', /.+/)
  const sizeAtDefault = await html.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))
  await page.getByLabel('Page width', { exact: true }).selectOption('wide')
  await page.getByLabel('Text size', { exact: true }).selectOption('large')
  await expect(html).toHaveAttribute('data-width', 'wide')
  await expect(html).toHaveAttribute('data-text', 'large')
  expect(await html.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThan(sizeAtDefault)
  await page.reload()
  await expect(html).toHaveAttribute('data-width', 'wide')
  await expect(html).toHaveAttribute('data-text', 'large')
  await openSettingsGroups(page, 'Sharing and devices')
  await page.getByLabel('Page width', { exact: true }).selectOption('standard')
  await page.getByLabel('Text size', { exact: true }).selectOption('default')
  await expect(html).not.toHaveAttribute('data-width', /.+/)
  await expect(html).not.toHaveAttribute('data-text', /.+/)
})
