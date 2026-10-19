import { expect, test } from './fixtures'
import { openBenchTab } from './benchSections'
import { enableDeveloperMode } from './sourceHelpers'

// The Benchmark Lab page is behind Developer Mode.
test.beforeEach(async ({ page }) => {
  await enableDeveloperMode(page)
})

// A3: the Lab's transcription form carries line-timing settings (speech pad, split limits, refine
// switch) that travel with the arm, and the Arena shows each run's timing with a verdict that never
// trades a timing gain for a worse text score. The runs and the arena are mocked: the seeded library
// has no clips with reference times.
const SHOTS = process.env.TIMING_SHOTS_DIR

const timing = (within: number) => ({
  cases: 40, start_median_ms: within > 0.7 ? 90 : 240, end_median_ms: within > 0.7 ? 130 : 310,
  within, merged: 2, split: 1, missed: 0, silence: 0,
})
const run = (id: number, extra: Record<string, unknown>) => ({
  id, label: '', stage: 'transcription', engine: 'whisper', model: 'large-v3', prompt_version: '', arena_group: null,
  status: 'done', case_count: 40, scored_count: 40, passed_count: 38, error_count: 0, aggregate_score: 0.92,
  avg_latency_seconds: 4.2, total_cost_usd: 0, peak_vram_mb: null, note: null, created_at: '2026-10-18T00:00:00',
  finished_at: '2026-10-18T00:10:00', context_settings: {}, case_filter: {}, delta_vs_first: null, ...extra,
})

for (const theme of ['light', 'dark', 'sepia', 'oled']) {
  test(`Benchmark Lab: timing settings travel with the arm and the Arena judges timing (${theme})`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    const arenaRuns = [
      run(1, { timing: timing(0.55), timing_verdict: { state: 'baseline', text: 'The default these are compared with.', margin_within: 0.03, margin_score: 0.004 } }),
      run(2, { aggregate_score: 0.918, timing: timing(0.56), timing_verdict: { state: 'repeat', text: 'A repeat of the default: its difference sets the noise margin.', margin_within: 0.03, margin_score: 0.004 } }),
      run(3, { aggregate_score: 0.93, delta_vs_first: 0.01, timing: timing(0.82), timing_verdict: { state: 'gain', text: 'Timing is better than the default by more than the noise and the text score is not worse.', margin_within: 0.03, margin_score: 0.004, gain: 0.27, score_change: 0.01 } }),
      run(4, { aggregate_score: 0.88, delta_vs_first: -0.04, timing: timing(0.8), timing_verdict: { state: 'cer_worse', text: 'Timing is better but the text score is worse, so this is not accepted: a timing gain is never traded for worse accuracy.', margin_within: 0.03, margin_score: 0.004, gain: 0.25, score_change: -0.04 } }),
    ]
    const caseTiming = { lines: 1, missed: false, split: false, merged: false, start_error_s: 0.09, end_error_s: 0.13, silence: 0 }
    await page.route('**/api/benchmark/runs?*', (r) => r.fulfill({ json: { runs: arenaRuns } }))
    await page.route('**/api/benchmark/arena?*', (r) => r.fulfill({
      json: {
        runs: arenaRuns, left_out: { total: 0, removed: 0, excluded: 0, edited: 0, not_in_every_run: 0 },
        rows: [{ case_id: 1, label: 'Moon · line 4', source_text: null, reference_text: 'こんにちは', tier: 'application', ref_version: 1,
          results: arenaRuns.map((a) => ({ case_id: 1, case_label: 'Moon · line 4', output_text: 'こんにちは', score: a.aggregate_score, metric: 'cer', passed: true,
            duration_seconds: 1, cost_usd: 0, error: null, timing: caseTiming })) }],
      },
    }))
    await page.goto('/#/benchmark')
    await openBenchTab(page, 'Run a benchmark')
    const card = page.getByRole('region', { name: 'Run a benchmark' })
    await card.getByLabel('Stage', { exact: true }).selectOption('transcription')
    const fold = card.getByTestId('bench-real-settings')
    await fold.locator('summary').click()
    await expect(fold.getByLabel('Speech pad', { exact: true })).toHaveValue('400')
    await expect(fold.getByLabel('Longest line', { exact: true })).toHaveValue('8')
    await fold.getByLabel('Speech pad', { exact: true }).fill('150')
    await expect(fold.locator('summary')).toContainText('1 changed')
    if (SHOTS) await fold.screenshot({ path: `${SHOTS}/timing-settings-${theme}-desktop.png` })
    const sent = page.waitForRequest((r) => r.url().endsWith('/api/benchmark/estimate'))
    await card.getByRole('button', { name: 'Estimate cost' }).click()
    expect((await sent).postDataJSON().configs[0].options).toEqual({ speech_pad_ms: 150 })

    await openBenchTab(page, 'Recent runs')
    const runs = page.getByRole('region', { name: 'Recent runs' })
    for (const id of [1, 2, 3, 4]) await runs.getByRole('checkbox').nth(id - 1).check()
    await runs.getByRole('button', { name: 'Compare in Arena (4)' }).click()
    const arena = page.getByRole('region', { name: 'Model Arena' })
    await expect(arena.getByTestId('arena-timing').first()).toContainText('Timing on 40 lines: start 240 ms, end 310 ms off (median) · 55% within 0.3 s')
    await expect(arena.getByTestId('arena-timing-verdict').nth(3)).toContainText('never traded for worse accuracy')
    await expect(arena.getByTestId('result-timing').first()).toHaveText('Timing: start 90 ms, end 130 ms off')
    if (SHOTS) await arena.screenshot({ path: `${SHOTS}/timing-arena-${theme}-desktop.png` })
  })
}
