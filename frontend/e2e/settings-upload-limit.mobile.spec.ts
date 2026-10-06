import { expect, test } from '@playwright/test'
import { mockUploadSettings } from './uploadLimitMocks'
import { openSettingsGroups } from './settingsNav'

// Phone layout of Settings > Advanced > Uploads: no sideways scroll, 44px buttons.

test('upload limit block fits a phone', async ({ page }) => {
  const { unmocked } = await mockUploadSettings(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const s = page.locator('details.section:has(> summary > .section-title:text-is("Uploads"))')
  await s.locator('summary').click()
  await expect(s).toHaveAttribute('open', '')
  const input = s.getByLabel('Upload size limit (MB)', { exact: true })
  await expect(input).toBeVisible()
  await input.fill('4096')
  const save = s.getByRole('button', { name: 'Save' })
  const box = await save.boundingBox()
  expect(box && box.height).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(unmocked).toEqual([])
})
