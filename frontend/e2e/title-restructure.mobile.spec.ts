import { expect, test } from '@playwright/test'

import { mockRestructure, openRestructure } from './titleRestructureMocks'

test('Merge or move chapters fits a phone screen', async ({ page }) => {
  await mockRestructure(page)
  await openRestructure(page)
  await page.getByTestId('title-restructure').getByLabel('Move to', { exact: true }).selectOption({ label: 'Heaven Official (part 2)' })
  await page.getByRole('checkbox', { name: /Chapter 2/ }).check()
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('title-restructure-preview')).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})
