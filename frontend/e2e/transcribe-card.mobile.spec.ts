import { expect, test, type Page } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'
import { transcribeCard, openAdvanced } from './sourceHelpers'
import { expectNoSidewaysScroll } from './mockHelpers'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the Transcribe card's callout and flagged
// field fit the width and keep 44px touch targets.

const SHOTS = process.env.SHOT_DIR

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

test('the "Still needed" callout fits and its button is 44px tall', async ({ page }) => {
  await page.goto('/#/drama/1/source?tab=transcribe')
  const callout = page.locator('#transcribe-needed')
  await expect(callout).toBeVisible()
  expect(await hitHeight(callout.getByRole('button', { name: 'Paste transcript' }))).toBeGreaterThanOrEqual(44)
  await expectNoSidewaysScroll(page)
  await shot(page, 'transcribe-callout-phone')
})

test('a refused option is flagged on the phone without sideways scroll', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/1/run', (route) =>
    route.fulfill({
      status: 422,
      json: { error: { code: 'validation_error', message: 'Qwen3 forced alignment needs a transcript to align, but this title is in Whisper-text-only mode.' } },
    }),
  )
  await page.goto('/#/drama/1/source?tab=transcribe')
  await page.getByLabel('Transcript text').fill('line one')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByLabel('Alignment method', { exact: true })).toHaveAttribute('aria-invalid', 'true')
  await expectNoSidewaysScroll(page)
  await shot(page, 'transcribe-field-error-phone')
})

test('folded settings on the phone', async ({ page }) => {
  await page.goto('/#/drama/1/source?tab=transcribe')
  await expect(page.getByTestId('settings-summary')).toBeVisible()
  const card = transcribeCard(page)
  await expect(card.getByLabel('Source language', { exact: true })).toBeVisible()
  await expect(card.getByLabel('Accuracy and speed', { exact: true })).toBeVisible()
  await expect(card.getByLabel('Expected speakers', { exact: true })).toHaveCount(0)
  const tools = await openAdvanced(page)
  await expect(tools.getByLabel('Expected speakers', { exact: true })).toBeVisible()
  await expectNoSidewaysScroll(page)
  await shot(page, 'transcribe-folded-phone')
})
