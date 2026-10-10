import { expect, test } from '@playwright/test'

// Phone width: the sticky header wraps or collapses, the page never scrolls sideways and the 16px gutters hold.

test('the header stays in view on a long page with no sideways scroll', async ({ page }) => {
  await page.goto('/#/library')
  await expect(page.getByTestId('stats')).toBeVisible()
  await page.evaluate(() => { (document.querySelector('.app-main') as HTMLElement).style.minHeight = '4000px' })
  await page.evaluate(() => window.scrollTo(0, 1500))
  const header = page.locator('.app-header')
  await expect(header).toBeInViewport({ ratio: 1 })
  const box = (await header.boundingBox())!
  expect(box.y).toBeLessThanOrEqual(1)
  expect(box.x, 'left gutter').toBeGreaterThanOrEqual(15)
  expect(box.x + box.width, 'right gutter').toBeLessThanOrEqual(390 - 15)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await expect(page.getByRole('button', { name: 'Report a problem' })).toBeInViewport({ ratio: 1 })
})
