import { expect, test } from '@playwright/test'

import { mockCompare } from './compareMocks'
import { openFoldFor } from './reviewFolds'
import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// Review > Compare transcription: choose lines, other model/backend, optional
// translation; proposals sit in a table and nothing is sent to apply until
// "Use this" / "Use all shown".

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function open(page: import('@playwright/test').Page) {
  await page.goto('/#/drama/3/review')
  await openFoldFor(page, 'Compare transcription')
  await page.locator('summary').filter({ hasText: /^Compare transcription/ }).click()
  const box = page.getByTestId('compare-transcription')
  await expect(box).toBeVisible()
  return box
}

test('runs with chosen settings, shows highlighted diffs, applies only what is chosen', async ({ page }) => {
  const seen = await mockCompare(page)
  const box = await open(page)

  // Unavailable backends are disabled with their reason once chosen; defaults are the saved ones.
  await expect(box.getByLabel('Whisper model')).toHaveValue('small')
  await expect(box.getByLabel('ASR backend')).toHaveValue('whisper')
  await expect(box.getByRole('option', { name: /Qwen3 ASR \(unavailable\)/ })).toBeDisabled()

  await box.getByLabel('Which lines').selectOption('range')
  await box.getByLabel('From line #').fill('1')
  await box.getByLabel('To line #').fill('3')
  await box.getByLabel('Whisper model').selectOption('tiny')
  await box.getByRole('switch', { name: 'Also translate' }).click()
  await expect(box.getByTestId('compare-estimate')).toContainText('3 lines · translation about $0.04 (cap $5.00)')

  await box.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(box.getByTestId('compare-progress')).toContainText('Line 2 of 3')
  expect(seen.runs).toEqual([{
    selection: { kind: 'range', from_number: 1, to_number: 3 }, whisper_size: 'tiny', asr_backend: 'whisper',
    translate: true, retranslate_current: false,
  }])

  const rows = box.getByTestId('compare-row')
  await expect(rows).toHaveCount(2) // line 2 heard the same as now
  await expect(box.getByTestId('compare-results')).toContainText('1 line heard the same as now.')
  await expect(rows.nth(0).locator('mark')).toHaveText(['了', '啦', 'is here', 'has arrived'])
  expect(seen.applies).toEqual([]) // nothing written yet

  // Use this on line 3 with its English; line 1 stays out.
  await rows.nth(1).getByLabel('Use English').check()
  await rows.nth(1).getByRole('button', { name: 'Use this' }).click()
  await expect(box.getByTestId('compare-note')).toHaveText('Replaced 1 line.')
  expect(seen.applies).toEqual([{ job_id: 'comparetx_3', items: [
    { line_id: 13, expected_base_zh: '再见', expected_candidate_zh: '在见', use_english: true, expected_candidate_en: 'See it' },
  ] }])
  await expect(rows).toHaveCount(1)

  await box.getByRole('button', { name: 'Use all shown' }).click()
  // The click returns before the mocked request is recorded.
  await expect.poll(() => seen.applies[1]).toEqual({ job_id: 'comparetx_3', items: [
    { line_id: 11, expected_base_zh: '魏婴来了', expected_candidate_zh: '魏婴来啦', use_english: false, expected_candidate_en: '' },
  ] })
})

test('the line cap and a bad range are explained and block the run', async ({ page }) => {
  await mockCompare(page, { estimate: { line_count: 250 } })
  const box = await open(page)
  await box.getByLabel('Which lines').selectOption('range')
  await box.getByLabel('From line #').fill('5')
  await box.getByLabel('To line #').fill('2')
  await expect(box.getByTestId('compare-blocked')).toContainText('must not be after the last')
  await expect(box.getByRole('button', { name: 'Compare', exact: true })).toBeDisabled()
  await box.getByLabel('To line #').fill('300')
  await expect(box.getByTestId('compare-blocked')).toContainText('250 lines; one run is limited to 200')
  await expect(box.getByRole('button', { name: 'Compare', exact: true })).toBeDisabled()
})

test('a spent monthly cap is shown before starting', async ({ page }) => {
  await mockCompare(page, { estimate: { monthly_refusal: true } })
  const box = await open(page)
  await box.getByRole('switch', { name: 'Also translate' }).click()
  await expect(box.getByTestId('compare-blocked')).toContainText('spending cap is used up')
  await expect(box.getByRole('button', { name: 'Compare', exact: true })).toBeDisabled()
})

test('sends the hint and names, prefilled from the saved names, and shows what was used', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.draft.3.transcribe', JSON.stringify({ v: 1, values: { extraNames: '沈清疑' } })))
  const seen = await mockCompare(page)
  const box = await open(page)
  await box.getByLabel('Which lines').selectOption('range')
  await box.getByLabel('From line #').fill('1')
  await box.getByLabel('To line #').fill('3')
  await expect(box.getByRole('textbox', { name: 'Extra character names' })).toHaveValue('沈清疑')
  await box.getByRole('textbox', { name: 'Extra character names' }).fill('沈清疑、云隐宗')
  await box.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(box.getByTestId('compare-results')).toBeVisible()
  expect(seen.runs).toMatchObject([{ extra_names: '沈清疑、云隐宗' }])
  await expect(box.getByTestId('compare-prompt-used')).toHaveText('Extra names used: 沈清疑、云隐宗')

  await box.getByRole('textbox', { name: 'Hint for the model (names, terms)' }).fill('云隐宗')
  await box.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(box.getByTestId('compare-prompt-used')).toHaveText('Hint used: 云隐宗')
  expect(seen.runs[1]).toMatchObject({ initial_prompt: '云隐宗' })
  expect(seen.runs[1]).not.toHaveProperty('extra_names')
})
