import { expect, test } from '@playwright/test'
import { openBenchTab } from './benchSections'
import { enableDeveloperMode } from './sourceHelpers'

// The Benchmark Lab page is behind Developer Mode.
test.beforeEach(async ({ page }) => {
  await enableDeveloperMode(page)
})

// Benchmark Lab: your own clips, uploaded in the browser and checked in a table, then cleaned up.
// Transcription needs a Whisper model, so the runs here use the clips' translation cases and the free
// offline engine (output "[TEST] <source>"). Named lab-* for the reason lab-benchmark.spec.ts gives.

const SHOTS = process.env.BENCH_SHOTS_DIR
const audio = (name: string) => ({ name, mimeType: 'audio/wav', buffer: Buffer.from('RIFF....WAVEfmt ') })

test('Benchmark Lab: upload clips, fix a reference, exclude, compare on shared clips, export, delete a run', async ({ page }) => {
  const setName = `clips-${Date.now()}`
  await page.goto('/#/benchmark')
  await openBenchTab(page, 'Golden sets')
  const sets = page.getByRole('region', { name: 'Golden sets' })
  const clips = sets.locator('details', { hasText: 'Your own clips' })

  // Nothing to save until there are files, a set name and a transcript for every clip.
  await expect(clips.getByRole('button', { name: 'Save clips' })).toBeDisabled()
  await clips.getByLabel('Clip set name', { exact: true }).fill(setName)
  await clips.getByLabel('Title for new clips', { exact: true }).fill('Moon Drama')
  await clips.getByLabel('Audio files', { exact: true }).setInputFiles([audio('one.wav'), audio('two.wav'), audio('notes.txt')])
  await expect(clips.getByRole('list', { name: 'Clips to save' }).locator(':scope > li')).toHaveCount(2)
  await expect(clips).toContainText('Left out 1 file')
  await expect(clips.getByRole('button', { name: 'Save clips' })).toBeDisabled()
  await expect(clips).toContainText('2 clips still need a transcript.')

  // Paste text for many rows: transcript, a Tab, then the translation.
  await clips.getByLabel('Paste text for many clips', { exact: true }).fill('你好\tHello\nありがとう\tThank you')
  await clips.getByRole('button', { name: 'Fill rows' }).click()
  await expect(clips.getByLabel('Transcript of two.wav')).toHaveValue('ありがとう')

  // Kana under a Chinese label is warned about, naming the clip; the label is not changed.
  const warning = clips.getByRole('note').filter({ hasText: 'points to Japanese' })
  await expect(warning).toHaveCount(1)
  await expect(clips.getByLabel('Language of two.wav')).toHaveValue('zh')
  await clips.getByLabel('Language of two.wav').selectOption('ja')
  await expect(warning).toHaveCount(0)

  if (SHOTS) await page.screenshot({ path: `${SHOTS}/clips-table-desktop.png`, fullPage: true })

  await expect(clips.getByRole('button', { name: 'Save clips' })).toBeEnabled()
  await clips.getByRole('button', { name: 'Save clips' }).click()
  await expect(clips.getByRole('status').filter({ hasText: 'Added 2 clips' })).toContainText('2 transcription, 2 translation')
  const transcription = sets.getByRole('row', { name: new RegExp(`${setName}.*Transcription`) })
  await expect(transcription).toContainText('2')

  // Fix a transcript in place: the change is logged and the clip's translation source follows.
  await sets.getByRole('button', { name: `Show cases in ${setName} (Application)` }).first().click()
  const cases = sets.getByRole('region', { name: `Cases in ${setName}` })
  await cases.getByRole('button', { name: 'Edit reference of Moon Drama #1' }).click()
  await cases.getByLabel('Reference of Moon Drama #1').fill('你好，朋友')
  await cases.getByRole('button', { name: 'Save reference' }).click()
  await expect(cases).toContainText('Reference v2')
  await cases.getByRole('button', { name: 'Changes to the reference of Moon Drama #1' }).click()
  await expect(cases.getByRole('list', { name: 'Changes to Moon Drama #1' })).toContainText('你好，朋友')

  // Exclude and bring back.
  await cases.getByRole('button', { name: 'Exclude Moon Drama #2' }).click()
  await expect(cases).toContainText('Excluded')
  await cases.getByRole('button', { name: 'Include Moon Drama #2' }).click()
  await expect(cases).not.toContainText('Excluded')

  // Two runs of the clips' translation cases with the offline engine, before and after a second fix.
  const runOnce = async (label: string) => {
    await openBenchTab(page, 'Run a benchmark')
    const card = page.getByRole('region', { name: 'Run a benchmark' })
    await card.getByLabel('Run label', { exact: true }).fill(label)
    await card.getByLabel('Stage', { exact: true }).selectOption('translation')
    await card.getByLabel('Tier', { exact: true }).selectOption('application')
    await card.getByLabel('Golden set', { exact: true }).selectOption(setName)
    await card.getByLabel('Engine 1', { exact: true }).selectOption('fake')
    await card.getByRole('button', { name: 'Estimate cost' }).click()
    await expect(card.getByTestId('bench-estimate')).toContainText('Estimate for 2 cases')
    await card.getByRole('button', { name: 'Start run' }).click()
    await openBenchTab(page, 'Recent runs')
    await expect(page.getByRole('region', { name: 'Recent runs' }).getByRole('row', { name: new RegExp(label) }))
      .toContainText('Done', { timeout: 15_000 })
  }
  await runOnce('clips A')
  await openBenchTab(page, 'Golden sets')
  await sets.getByRole('button', { name: `Show cases in ${setName} (Application)` }).last().click()
  await sets.getByRole('region', { name: `Cases in ${setName}` }).getByRole('button', { name: /^Edit reference of .* #2$/ }).last().click()
  await sets.getByRole('region', { name: `Cases in ${setName}` }).getByLabel(/^Reference of .* #2$/).fill('Thanks a lot')
  await sets.getByRole('region', { name: `Cases in ${setName}` }).getByRole('button', { name: 'Save reference' }).click()
  await runOnce('clips B')

  // The comparison scores only the clip whose reference did not change between the runs, and says so.
  const runs = page.getByRole('region', { name: 'Recent runs' })
  await runs.getByRole('checkbox', { name: /clips A$/ }).check()
  await runs.getByRole('checkbox', { name: /clips B$/ }).check()
  await runs.getByRole('button', { name: 'Compare in Arena (2)' }).click()
  const arena = page.getByRole('region', { name: 'Model Arena' })
  await expect(arena.getByTestId('arena-left-out')).toContainText('Compared on the 1 case')
  await expect(arena.getByTestId('arena-left-out')).toContainText('1 left out: 1 edited between the runs')
  await expect(arena.getByRole('list', { name: 'Cases' }).locator(':scope > li')).toHaveCount(1)
  await expect(arena.getByRole('link', { name: 'Export CSV' })).toHaveAttribute('href', /\/api\/benchmark\/arena\/export\?run_ids=\d+&run_ids=\d+&format=csv/)
  if (SHOTS) {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.screenshot({ path: `${SHOTS}/clips-arena-desktop.png`, fullPage: true })
  }

  // Delete the first run with a confirmation; its results and its place in the list go.
  await arena.getByRole('button', { name: 'Close' }).click()
  await runs.getByRole('row', { name: /clips A/ }).getByRole('button', { name: /^Delete run/ }).click()
  await runs.getByRole('button', { name: /^Confirm delete run/ }).click()
  await expect(runs.getByRole('row', { name: /clips A/ })).toHaveCount(0)
  await expect(runs.getByRole('row', { name: /clips B/ })).toHaveCount(1)
})
