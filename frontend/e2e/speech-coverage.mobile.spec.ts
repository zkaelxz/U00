import { expect, test } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'
import { openFoldFor } from './reviewFolds'
import { openTranscribeOptions } from './sourceHelpers'
import { mockSpeechCoverage } from './speechCoverageMocks'

// Phone project (390x844, touch): gaps are cards that fit the width and keep 44px touch targets.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('gap cards fit the phone and their buttons are 44px tall', async ({ page }) => {
  await mockSpeechCoverage(page)
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  await openFoldFor(page, 'Speech coverage')
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: /^Speech coverage$/ }) })
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
  const check = page.getByRole('button', { name: 'Check coverage' })
  expect(await hitHeight(check)).toBeGreaterThanOrEqual(44)
  await check.click()
  const cards = page.getByTestId('coverage-gaps').locator('li')
  await expect(cards).toHaveCount(2)
  await expect(cards.nth(1).getByTestId('gap-raw')).toHaveText('Whisper produced nothing here')
  expect(await hitHeight(cards.nth(0).getByRole('button', { name: /^Play/ }))).toBeGreaterThanOrEqual(44)
  expect(await hitHeight(cards.nth(0).getByRole('link', { name: 'Open Review' }))).toBeGreaterThanOrEqual(44)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
