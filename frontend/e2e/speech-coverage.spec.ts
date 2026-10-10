import { expect, test } from '@playwright/test'
import { openFoldFor } from './reviewFolds'
import { openTranscribeOptions } from './sourceHelpers'
import { mockSpeechCoverage } from './speechCoverageMocks'

test.use({ viewport: { width: 1280, height: 800 } })

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function openPanel(page: import('@playwright/test').Page) {
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  await openFoldFor(page, 'Speech coverage')
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: /^Speech coverage$/ }) })
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

test('is disabled with its reason when the title has no audio', async ({ page }) => {
  await openPanel(page)
  await expect(page.getByRole('button', { name: 'Check coverage' })).toBeDisabled()
  await expect(page.getByTestId('speech-coverage')).toContainText('Still needed: audio on this title.')
})

test('checks on demand and lists the gaps with what Whisper produced', async ({ page }) => {
  const seen = await mockSpeechCoverage(page)
  await openPanel(page)
  await page.getByRole('button', { name: 'Check coverage' }).click()
  await expect(page.getByTestId('coverage-totals')).toHaveText('2:30 of 3:20 of speech has a line (75%).')
  expect(seen.posts).toEqual(['{"min_gap_seconds":2}'])
  const rows = page.getByTestId('coverage-gaps').locator('tbody tr')
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(0)).toContainText('1:05–1:11')
  await expect(rows.nth(0).getByTestId('gap-raw')).toHaveText('Whisper produced text here but it was lost afterwards')
  await expect(rows.nth(0)).toContainText('好的好的')
  await expect(rows.nth(1).getByTestId('gap-raw')).toHaveText('Whisper produced nothing here')
  await expect(rows.nth(1).getByRole('button', { name: 'Play 5:00–5:04' })).toBeVisible()
  await expect(rows.nth(0).getByRole('link', { name: 'Open Review' })).toHaveAttribute('href', /#\/drama\/1\/review/)
  await expect(page.getByRole('button', { name: 'Check again' })).toBeEnabled()
})
