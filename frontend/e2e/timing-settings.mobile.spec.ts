import { expect, test } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'
import { mockTimingSettings } from './timingSettingsMocks'

// Phone layout of Settings > Performance and advanced > Timing and splitting: no sideways scroll, 44px buttons.

test('the timing card fits a phone', async ({ page }) => {
  const { unmocked } = await mockTimingSettings(page, { developerMode: true })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Performance and advanced')
  const card = page.getByRole('region', { name: 'Timing and splitting' })
  await expect(card).toBeVisible()
  const longest = card.getByLabel('Longest line (time)', { exact: true })
  await longest.fill('6')
  for (const name of ['Save', 'Reset Longest line (time) to default']) {
    const box = await card.getByRole('button', { name }).boundingBox()
    expect(box && box.height).toBeGreaterThanOrEqual(44)
  }
  await expect(card.getByTestId('timing-default-split_max_seconds')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(unmocked).toEqual([])
})
