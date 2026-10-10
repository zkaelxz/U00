import { expect, test } from '@playwright/test'

import { openTranscribeOptions } from './sourceHelpers'

test('on a phone the basic view fits and the controls are 44px tall', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = page.getByRole('region', { name: 'Transcribe' })
  for (const l of ['Source language', 'Whisper model']) {
    const box = await card.getByLabel(l, { exact: true }).boundingBox()
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(43.5)
  }
  const box = await card.getByRole('button', { name: 'Transcribe', exact: true }).boundingBox()
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(43.5)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('the Translate primary sits above the options on a phone, with one engine picker', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByLabel('AI engine', { exact: true })).toHaveCount(1)
  const go = await run.locator('button.primary').first().boundingBox()
  const fold = await run.getByText('More options', { exact: true }).boundingBox()
  expect(go && fold && go.y < fold.y).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
