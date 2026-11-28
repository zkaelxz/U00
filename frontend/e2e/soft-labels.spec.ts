import { expect, test } from './fixtures'
import type { Locator, Page } from '@playwright/test'

import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// Soft's rounder font is wider than the other looks, so a label sized for them was cut with an ellipsis here.

const soft = (page: Page) => page.addInitScript(() => {
  localStorage.setItem('baihe.style', 'soft')
  localStorage.setItem('baihe.navDock', 'docked')
})

// An input scrolls its placeholder silently, so compare the text's width with the room inside the box.
async function placeholderFits(input: Locator) {
  return input.evaluate((el: HTMLInputElement) => {
    const cs = getComputedStyle(el)
    const ctx = document.createElement('canvas').getContext('2d')!
    ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`
    const room = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
    return room - ctx.measureText(el.placeholder).width
  })
}

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

for (const theme of ['light', 'dark'] as const) {
  test(`Soft ${theme}: the docked menu and the search boxes show their full text`, async ({ page }) => {
    await soft(page)
    await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    await page.goto('/')
    await expect(page.getByTestId('drama-count')).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-style', 'soft')
    const label = page.getByRole('button', { name: 'Minimize navigation' }).locator('.rail-label')
    await expect(label).toHaveText('Minimize navigation')
    expect(await label.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0)
    expect(await placeholderFits(page.getByRole('searchbox', { name: /Search titles/ }))).toBeGreaterThanOrEqual(0)

    await page.goto('/#/drama/3/review')
    const search = page.getByPlaceholder('Search source or translation')
    await expect(search).toBeVisible()
    expect(await placeholderFits(search)).toBeGreaterThanOrEqual(0)
  })
}
