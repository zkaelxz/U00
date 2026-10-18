import { expect, test } from './fixtures'
import { openBenchTab } from './benchSections'
import { finished, mockLab } from './sweepMocks'

// A4: the Settings sweep tab. Estimate shows the halves, what is left out and the passes it can skip;
// Start sends the set and language; a running sweep shows its progress; a finished sweep shows the
// recommendation with the hold-out result, the margin rule and the per-tag breakdown, and Approve takes two
// presses. Everything is mocked: the seeded library has no audio clips and no model.
// Set SWEEP_SHOTS_DIR=<dir> to also save screenshots in each theme.
const SHOTS = process.env.SWEEP_SHOTS_DIR
// The sticky app header would cover the card's top in an element screenshot.
const NO_HEADER = '.app-header { visibility: hidden !important; }'

for (const theme of ['light', 'dark', 'sepia', 'oled']) {
  test(`Settings sweep: estimate, start, and a finished recommendation (${theme})`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    await mockLab(page, () => null)
    await page.goto('/#/benchmark')
    await openBenchTab(page, 'Run a benchmark')
    const card = page.getByRole('region', { name: 'Settings sweep' })
    await expect(card.getByLabel('Golden set', { exact: true })).toHaveValue('My drama clips')
    // Only sets with audio clips are offered.
    await expect(card.getByLabel('Golden set', { exact: true }).locator('option')).toHaveCount(1)
    await expect(card.getByTestId('sweep-start-reason')).toHaveText('Estimate this sweep first.')
    await card.getByRole('button', { name: 'Estimate', exact: true }).click()
    const est = card.getByTestId('sweep-estimate')
    await expect(est).toContainText('9 passes to run, about 45 min')
    await expect(est).toContainText('3 passes have already run on these clips and will be skipped.')
    await expect(est).toContainText('Held out to confirm: 2 titles')
    await expect(card.getByTestId('sweep-cost')).toHaveText("Cost $0.00: transcription runs on this PC, so the $20.00 monthly cap for paid engines doesn't apply.")
    await expect(est.getByRole('list', { name: 'Left out of this sweep' })).toContainText("large-v3-turbo left out: large-v3-turbo isn't downloaded")
    if (SHOTS) await card.screenshot({ path: `${SHOTS}/sweep-estimate-${theme}-desktop.png`, style: NO_HEADER })

    await page.route('**/api/jobs/benchmark_lab', (r) => r.fulfill({ json: {
      job_id: 'benchmark_lab', status: 'running', progress: 0.4, message: 'Sweep pass 4 of about 9 (large-v3, Beam size 3, tuning): whisper large-v3: clip 12',
      error: null, description: 'Benchmark sweep', gpu_touching: true, started_at: 1, finished_at: null, updated_at: 1 } }))
    const sent = page.waitForRequest((r) => r.url().endsWith('/api/benchmark/sweep') && r.method() === 'POST')
    await card.getByRole('button', { name: 'Resume sweep' }).click()
    expect((await sent).postDataJSON()).toEqual({ set_name: 'My drama clips', language: 'zh', confirm: true })
    await expect(card.getByTestId('bench-progress')).toContainText('Running · 40% · Sweep pass 4 of about 9')
    if (SHOTS) await card.screenshot({ path: `${SHOTS}/sweep-running-${theme}-desktop.png`, style: NO_HEADER })
  })

  test(`Settings sweep: the recommendation and approving it (${theme})`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    let sweep: Record<string, unknown> = finished
    await mockLab(page, () => sweep)
    let decided: unknown = null
    await page.route('**/api/benchmark/sweep/decision', (r) => {
      decided = r.request().postDataJSON()
      sweep = { ...finished, decision: { decision: 'promoted', reason: 'fewer split lines',
        decided_at: '2026-10-18T02:05:00', profile: { id: 7, name: 'Sweep My drama clips (zh) 2026-10-18 02:05' } } }
      return r.fulfill({ json: { sweep } })
    })
    await page.goto('/#/benchmark?tab=run')
    const card = page.getByRole('region', { name: 'Settings sweep' })
    const rec = card.getByTestId('sweep-recommendation')
    await expect(rec).toContainText('Change recommended')
    await expect(rec).toContainText('large-v3, Minimum silence (ms) 500')
    await expect(rec).toContainText('85.8% → 88.9% (3.1 CER points better)')
    await expect(rec).toContainText('at least 2 CER points and at least twice the noise')
    const tags = rec.getByRole('table', { name: 'Held-out score by tag' })
    await expect(tags.getByRole('row', { name: /music/ })).toContainText('Too few clips to decide')
    await expect(tags.getByRole('row', { name: /quiet/ })).not.toContainText('Too few')
    const applies = card.getByRole('list', { name: 'What applying the profile sets on a title' })
    await expect(card.getByTestId('sweep-applies')).toContainText('changes no title')
    await expect(applies.getByRole('listitem')).toHaveCount(7)
    await expect(applies).toContainText('Translation engineclaude')
    await expect(applies).toContainText('Default to she/heroff')
    if (SHOTS) await card.screenshot({ path: `${SHOTS}/sweep-recommendation-${theme}-desktop.png`, style: NO_HEADER })

    await card.getByLabel('Why?', { exact: true }).fill('fewer split lines')
    await card.getByRole('button', { name: 'Approve the winner' }).click()
    expect(decided).toBeNull()
    await card.getByRole('button', { name: 'Save as a setting profile' }).click()
    await expect(card.getByTestId('sweep-decision')).toContainText('Saved as the profile “Sweep My drama clips (zh) 2026-10-18 02:05”')
    await expect(card.getByTestId('sweep-applies')).toHaveCount(0)
    expect(decided).toEqual({ decision: 'approve', reason: 'fewer split lines', confirm: true })
    if (SHOTS) await card.screenshot({ path: `${SHOTS}/sweep-approved-${theme}-desktop.png`, style: NO_HEADER })
  })
}
