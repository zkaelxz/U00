import { expect, test } from '@playwright/test'

import { openGroup } from './source-groups'
import { mockSubtitleImport, SRT_FILE } from './subtitleImportMocks'

// Source stage > Import subtitle file on seeded drama 2 (desktop). Preview and import are faked.

async function open(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith('baihe.section.source.')) localStorage.removeItem(k)
  })
  await page.goto('/#/drama/2/source')
  await openGroup(page, 'Import subtitle file')
  return page.getByTestId('subtitle-import')
}

test('picking a file shows the encoding, cue count and problems; importing as source needs the replace confirmation', async ({ page }) => {
  const seen = await mockSubtitleImport(page)
  const panel = await open(page)
  await expect(panel.getByRole('button', { name: 'Import', exact: true })).toBeDisabled()
  await panel.getByLabel('Subtitle file').setInputFiles(SRT_FILE)
  await expect(panel.getByTestId('subtitle-import-summary')).toHaveText('SRT · 42 cues · read as gb18030 (guessed) · Chinese')
  const problems = panel.getByRole('list', { name: 'Problems found' })
  await expect(problems.getByRole('listitem')).toHaveCount(2)
  await expect(problems).toContainText('3 cues, first at cue 5')
  await expect(panel.getByTestId('subtitle-import-impact')).toHaveText('Replaces this title\'s 5 lines with 42 from the file.')

  const importButton = panel.getByRole('button', { name: 'Import', exact: true })
  await expect(importButton).toBeDisabled()
  await panel.getByLabel('Replace the 5 current lines (saved to history first)').check()
  await importButton.click()
  await expect(panel.getByRole('status').filter({ hasText: 'Imported 42 lines.' })).toBeVisible()
  expect(seen.applies).toHaveLength(1)
  expect(seen.applies[0]).toContain('confirm_replace_lines')
  expect(seen.retimes).toHaveLength(0) // re-alignment is off by default
})

test('translation mode re-checks the file and asks before overwriting; re-align starts the Re-time job', async ({ page }) => {
  const seen = await mockSubtitleImport(page)
  const panel = await open(page)
  await panel.getByLabel('Subtitle file').setInputFiles(SRT_FILE)
  await expect(panel.getByTestId('subtitle-import-summary')).toBeVisible()
  await panel.getByRole('radiogroup', { name: 'Import as' }).getByText('Translation').click()
  await expect(panel.getByTestId('subtitle-import-impact')).toHaveText('Puts text on 4 of 5 lines, matched by time. 2 cues match no line.')
  await panel.getByLabel('Text encoding', { exact: true }).selectOption('gb18030')
  await expect.poll(() => seen.previews.at(-1) ?? '').toContain('gb18030')
  await panel.getByLabel('Overwrite 1 existing translation (saved to history first)').check()
  await panel.getByRole('switch', { name: 'Re-align with the audio afterwards' }).click()
  await panel.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('Put text on 42 lines')
  await expect.poll(() => seen.retimes.length).toBe(1)
  expect(seen.retimes[0]).toEqual({ line_ids: [101, 102, 103] })
  expect(seen.applies[0]).toContain('confirm_overwrite')
})

test('a file that is not a subtitle file, or has blocking problems, cannot be imported', async ({ page }) => {
  await mockSubtitleImport(page, { blocking: true, problems: [{ code: 'negative_length', severity: 'error', message: 'A cue ends before it starts (1 cue, first at cue 2).', count: 1 }] })
  const panel = await open(page)
  await panel.getByLabel('Subtitle file').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('x') })
  await expect(panel.getByRole('alert')).toHaveText('Pick an SRT, VTT, ASS, SSA or LRC file.')
  await panel.getByLabel('Subtitle file').setInputFiles(SRT_FILE)
  await expect(panel.getByRole('list', { name: 'Problems found' })).toContainText('A cue ends before it starts')
  await expect(panel.getByRole('button', { name: 'Import', exact: true })).toBeDisabled()
})
