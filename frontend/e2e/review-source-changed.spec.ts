import { expect, test } from '@playwright/test'

import { mockSourceChanged } from './sourceChangedMocks'

// A line whose source text changed after it was translated keeps its English
// and says so: a "Source changed" badge on the row, the Flagged filter, the
// Review checks count with "Retranslate these" and "Keep English for all", and
// a one-line warning on the Translate and Export stages.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('the rows say "Source changed" in text and the Flagged filter shows them', async ({ page }) => {
  await mockSourceChanged(page)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(3)
  await expect(page.locator('.review-line[data-line-id="1"]').getByTestId('source-changed-badge')).toHaveText('Source changed')
  await expect(page.locator('.review-line[data-line-id="2"]').getByTestId('source-changed-badge')).toHaveCount(0)
  // The deep link the Translate and Export warnings use opens Review on Flagged.
  await page.goto('/#/drama/3/translate')
  await page.goto('/#/drama/3/review?focus=flagged')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(2)
})

test('Review checks: the count, "Retranslate these" on exactly those ids, two-step "Keep English for all"', async ({ page }) => {
  const seen = await mockSourceChanged(page)
  await page.goto('/#/drama/3/review')
  const panel = page.getByTestId('source-changed')
  await expect(panel.getByTestId('source-changed-count')).toHaveText('2 lines changed in the source since they were translated.')

  await panel.getByRole('button', { name: 'Retranslate these' }).click()
  await expect.poll(() => seen.runs.length).toBe(1)
  expect(seen.runs[0].line_ids).toEqual([1, 3])
  expect(seen.runs[0]).not.toHaveProperty('force_retranslate')
  expect(seen.runs[0]).not.toHaveProperty('bulk')
  await expect(panel.getByRole('status').filter({ hasText: 'Retranslating 2 lines' })).toBeVisible()
  await expect(panel.getByTestId('job-panel')).toBeVisible()
})

test('Keep English for all needs a second press', async ({ page }) => {
  const seen = await mockSourceChanged(page)
  await page.goto('/#/drama/3/review')
  const panel = page.getByTestId('source-changed')
  await panel.getByRole('button', { name: /Keep English for all/ }).click()
  expect(seen.keeps).toEqual([])
  await panel.getByRole('button', { name: 'Confirm: keep the English on 2 lines' }).click()
  await expect.poll(() => seen.keeps).toEqual([{ line_ids: [1, 3] }])
  await expect(page.getByTestId('source-changed')).toHaveCount(0)
  await expect(page.locator('[data-testid="source-changed-badge"]')).toHaveCount(0)
})

test('one line keeps its English from the row', async ({ page }) => {
  await mockSourceChanged(page)
  const dismissed: string[] = []
  await page.route('**/api/lines/dramas/3/lines/1/dismiss-flag', (route) => {
    dismissed.push(route.request().url())
    return route.fulfill({
      json: { id: 1, idx: 0, start: 1, end: 2, zh: '句1', en: 'Line 1', speaker: null, speaker_manual: false,
        sfx: false, flag: null, flag_note: '', dub_filename: null, lang: null },
    })
  })
  await page.goto('/#/drama/3/review')
  const row = page.locator('.review-line[data-line-id="1"]')
  await row.locator('.review-zh').click()
  await row.getByRole('button', { name: 'Keep English' }).click()
  await expect.poll(() => dismissed.length).toBe(1)
})

test('Translate and Export show the count with one link to the Review filter', async ({ page }) => {
  await mockSourceChanged(page)
  for (const stage of ['translate', 'export']) {
    await page.goto(`/#/drama/3/${stage}`)
    const warning = page.getByTestId('source-changed-warning')
    await expect(warning).toHaveText('2 lines changed in the source since they were translated. Review them')
    await expect(warning.getByRole('link')).toHaveAttribute('href', '#/drama/3/review?focus=flagged')
  }
  await page.getByTestId('source-changed-warning').getByRole('link').click()
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(2)
})
