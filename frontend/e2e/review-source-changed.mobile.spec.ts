import { expect, test } from '@playwright/test'

import { mockSourceChanged } from './sourceChangedMocks'

// Phone: the "Source changed" badge, the Review checks count and both actions
// fit the screen with no sideways scroll.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('phone: badge, count and both actions on screen', async ({ page }) => {
  await mockSourceChanged(page)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line[data-line-id="1"]').getByTestId('source-changed-badge')).toHaveText('Source changed')
  const panel = page.getByTestId('source-changed')
  await expect(panel.getByTestId('source-changed-count')).toHaveText('2 lines changed in the source since they were translated.')
  for (const name of ['Retranslate these', /Keep English for all/]) {
    const button = panel.getByRole('button', { name })
    await button.scrollIntoViewIfNeeded()
    await expect(button).toBeVisible()
    const box = (await button.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(390)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
