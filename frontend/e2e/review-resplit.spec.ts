import { expect, test } from '@playwright/test'

import { SUMMARY, clearLines, mockResplit, openResplit, seedLines } from './resplitMocks'

// Re-split long lines in the Review stage. Lines are real (seeded); the
// re-split, reassign and job routes are mocked.

test.beforeEach(() => seedLines())
test.afterAll(() => clearLines())

test('splits with estimated timing and shows the summary', async ({ page }) => {
  const calls = await mockResplit(page, () => ({ json: SUMMARY }))
  const group = await openResplit(page)
  await group.getByRole('button', { name: 'Re-split long lines' }).click()
  await expect(group.getByTestId('resplit-summary')).toHaveText('Split 31 lines into 118; speakers re-assigned.')
  expect(calls.resplits).toHaveLength(1)
  expect(calls.resplits[0]).toMatchObject({ align_to_audio: false, confirm: false })
  expect(calls.resplits[0].expected_line_ids).toHaveLength(3)
})

test('translated long lines ask for confirmation, then split with confirm', async ({ page }) => {
  const calls = await mockResplit(page, (_b, n) =>
    n === 1
      ? { status: 422, json: { error: { code: 'validation_error', message: 'Some long lines already have a translation. -- pass confirm=true.' } } }
      : { json: { ...SUMMARY, cleared_translations: 4 } },
  )
  const group = await openResplit(page)
  await group.getByRole('button', { name: 'Re-split long lines' }).click()
  await expect(group.getByTestId('resplit-confirm')).toBeVisible()
  await group.getByRole('button', { name: 'Split anyway' }).click()
  await expect(group.getByTestId('resplit-summary')).toHaveText(
    'Split 31 lines into 118; speakers re-assigned; 4 translations cleared.',
  )
  expect(calls.resplits.map((b) => b.confirm)).toEqual([false, true])
})

test('align to audio starts a job and reports its result with the fallback note', async ({ page }) => {
  const calls = await mockResplit(
    page,
    () => ({ json: { job_id: 'resplit_3', drama_id: 3 } }),
    { ...SUMMARY, note: 'The Qwen3 forced aligner is not available; lines were split with estimated timing.' },
  )
  const group = await openResplit(page)
  await group.getByRole('switch', { name: 'Align to audio' }).click()
  await group.getByRole('button', { name: 'Re-split long lines' }).click()
  await expect(group.getByTestId('resplit-summary')).toHaveText(
    'Split 31 lines into 118; speakers re-assigned. The Qwen3 forced aligner is not available; lines were split with estimated timing.',
  )
  expect(calls.resplits[0]).toMatchObject({ align_to_audio: true })
})

test('re-assigns speakers from the saved detection', async ({ page }) => {
  const calls = await mockResplit(page, () => ({ json: SUMMARY }))
  const group = await openResplit(page)
  await group.getByRole('button', { name: 'Re-assign speakers from saved detection' }).click()
  await expect(group.getByTestId('resplit-summary')).toHaveText(
    'Speakers re-assigned from the saved detection: 12 changed, 2 kept as you set them.',
  )
  expect(calls.reassigns).toBe(1)
})

test('shows speaking time per speaker from the saved detection', async ({ page }) => {
  await mockResplit(page, () => ({ json: SUMMARY }))
  await page.route('**/api/diarization/dramas/3/config', (route) =>
    route.fulfill({
      json: {
        drama_id: 3, hf_token_configured: false, expected_speakers: null, min_speakers: null, max_speakers: null,
        last_device: null, audio_available: false, manual_speaker_count: 0,
        speaker_summary: {
          speakers: [{ label: 'Anna', seconds: 220, percent: 84.6, turns: 41 }, { label: 'Bo', seconds: 40, percent: 15.4, turns: 1 }],
          total_speech_seconds: 260, uncovered_seconds: null,
        },
      },
    }),
  )
  const group = await openResplit(page)
  await expect(group.getByRole('list', { name: 'Speaking time per speaker' }).locator('li')).toHaveText([
    'Anna  3:40 · 84.6% · 41 turns',
    'Bo  0:40 · 15.4% · 1 turn',
  ])
})
