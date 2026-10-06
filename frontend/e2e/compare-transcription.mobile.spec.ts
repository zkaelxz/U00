import { expect, test } from '@playwright/test'

import { mockCompare } from './compareMocks'
import { openFoldFor } from './reviewFolds'
import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// Phone project (390x844, touch): the Compare transcription panel and its
// results fit the width (cards, not a wide table) and keep 44 px targets.

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('phone: panel and proposals fit the screen with 44 px targets', async ({ page }) => {
  await mockCompare(page)
  await page.goto('/#/drama/3/review')
  await openFoldFor(page, 'Compare transcription')
  await page.locator('summary').filter({ hasText: /^Compare transcription/ }).click()
  const box = page.getByTestId('compare-transcription')
  await box.getByLabel('Which lines').selectOption('range')
  await box.getByLabel('From line #').fill('1')
  await box.getByLabel('To line #').fill('3')
  await box.getByRole('switch', { name: 'Also translate' }).click()
  await expect(box.getByRole('textbox', { name: 'Hint for the model (names, terms)' })).toBeVisible()
  await expect(box.getByRole('textbox', { name: 'Extra character names' })).toBeVisible()
  await box.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(box.getByTestId('compare-row')).toHaveCount(2)

  const small = await box.evaluate((root) => {
    const sel = 'button:not(.link):not(.field-help-btn):not(.toggle), select, input:not([type=checkbox]), label:has(input[type=checkbox])'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
