import { expect, test, type Page } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'

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

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('the "Still needed" callout fits and its button is 44px tall', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  const callout = page.locator('#transcribe-needed')
  await expect(callout).toBeVisible()
  expect(await hitHeight(callout.getByRole('button', { name: 'Paste transcript' }))).toBeGreaterThanOrEqual(44)
  await expectNoHorizontalOverflow(page)
  await shot(page, 'transcribe-callout-phone')
})

test('a refused option is flagged on the phone without sideways scroll', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/1/run', (route) =>
    route.fulfill({
      status: 422,
      json: { error: { code: 'validation_error', message: 'Qwen3 forced alignment needs a transcript to align, but this drama is in Whisper-text-only mode.' } },
    }),
  )
  await page.goto('/#/drama/1/source')
  await page.getByLabel('Transcript text').fill('line one')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByLabel('Alignment method', { exact: true })).toHaveAttribute('aria-invalid', 'true')
  await expectNoHorizontalOverflow(page)
  await shot(page, 'transcribe-field-error-phone')
})

test('folded settings on the phone', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('settings-summary')).toBeVisible()
  const card = page.getByRole('region', { name: 'Transcribe' })
  await expect(card.getByLabel('Source language', { exact: true })).toBeVisible()
  await expect(card.getByLabel('Whisper model', { exact: true })).toBeHidden()
  await page.locator('.section-title', { hasText: /^More options$/ }).click()
  await expect(card.getByLabel('Whisper model', { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await shot(page, 'transcribe-folded-phone')
})
