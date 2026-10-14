import { expect, test } from './fixtures'

// The run's job is gone from the server (restart) while Recent runs still lists it as running:
// the page says so and offers a way to reconnect, instead of showing a run that never moves.
const activeRun = {
  id: 1, label: 'lost', stage: 'translation', engine: 'fake', model: null, prompt_version: null, arena_group: null,
  status: 'running', case_count: 3, scored_count: 0, passed_count: 0, error_count: 0, aggregate_score: null,
  avg_latency_seconds: null, total_cost_usd: null, peak_vram_mb: null, note: null, created_at: null, finished_at: null,
  context_settings: {}, case_filter: {}, delta_vs_first: null,
}

for (const theme of ['light', 'dark', 'sepia', 'oled']) {
  test(`Benchmark Lab says when it loses contact with the running job (${theme})`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    await page.route('**/api/benchmark/runs*', (r) => r.fulfill({ json: { runs: [activeRun] } }))
    await page.route('**/api/jobs/benchmark_lab', (r) => r.fulfill({ status: 404, json: { code: 'not_found', message: 'No such job.' } }))
    await page.goto('/#/benchmark')
    const lost = page.getByTestId('bench-lost-job')
    await expect(lost).toContainText('Lost contact with the running benchmark')
    await expect(lost.getByRole('button', { name: 'Reconnect' })).toBeVisible()
    const dir = process.env.LEFTOVERS_SHOTS_DIR
    if (dir) await page.screenshot({ path: `${dir}/after-benchmark-lost-job-${theme}-desktop.png` })
  })
}
