import { expect, test, type Page } from '@playwright/test'

import { mockReeval, mockRemote, overview } from './reevalMocks'

// Model re-evaluation card on the Benchmark Lab (Step 40b, desktop). The
// first test uses the real API for what spends nothing (read, add, estimate,
// reject, re-add): its production model is Claude with no key, so Run now
// stays disabled. Run, promote and the schedule go through a stand-in for
// /api/models/reeval* (reevalMocks.ts); any other non-GET call fails a test.
// Named lab-* on purpose (see lab-benchmark.spec.ts): a run leaves a
// "benchmark_lab" job on the shared e2e server and diagnostics.spec.ts
// expects an empty job list, so these specs must sort after it.
// Set REEVAL_SHOTS_DIR=<dir> to save a 1440x900 screenshot of the card.

const SHOTS = process.env.REEVAL_SHOTS_DIR
const LOCAL = { 'X-Baihe-Local': '1' }

const card = (page: Page) => page.getByRole('region', { name: 'Model re-evaluation' })

test('real API: add a free candidate, estimate, reject with a reason, re-adding shows the decision', async ({ page, request }) => {
  // One translation case so the estimate has something to price (nothing is spent).
  const imp = await request.post('/api/benchmark/import', {
    headers: LOCAL,
    data: { set_name: `reeval-${Date.now()}`, format: 'tsv', tier: 'application', source_language: 'zh', text: '你好\tHello' },
  })
  expect(imp.status()).toBe(200)

  const posts: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/models/reeval') && r.method() !== 'GET') posts.push(new URL(r.url()).pathname)
  })
  await page.goto('/#/benchmark')
  const c = card(page)
  await expect(c.getByTestId('reeval-production')).toContainText('Claude · claude-sonnet-5')
  await expect(c.getByTestId('reeval-production')).toContainText('from Settings')
  await expect(c).toContainText('No candidates yet.')
  await expect(c.getByRole('button', { name: 'Estimate cost' })).toBeDisabled()
  await expect(c.getByRole('button', { name: 'Run now' })).toBeDisabled()
  await expect(c.getByTestId('reeval-run-reason')).toHaveText('Add a candidate model first.')
  await expect(c.locator('summary', { hasText: 'Schedule and golden set' })).toContainText('Off · any set')
  await expect(c.locator('summary', { hasText: 'Decision history' })).toContainText('None yet')
  expect(posts).toEqual([])

  // Add NLLB (free): a new candidate.
  await c.getByLabel('Candidate engine', { exact: true }).selectOption('nllb')
  await c.getByLabel('Note', { exact: true }).fill('free fallback')
  await c.getByRole('button', { name: 'Add candidate' }).click()
  await expect(c.getByTestId('reeval-added')).toHaveText('Added NLLB as a candidate.')
  const list = c.getByRole('list', { name: 'Candidate models' })
  await expect(list.locator(':scope > li', { hasText: 'NLLB' })).toContainText('Open')

  // Estimate (spends nothing); production has no key, so Run now stays off and says why.
  await c.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(c.getByTestId('bench-estimate')).toContainText('Estimate for')
  await expect(c.getByRole('button', { name: 'Run now' })).toBeDisabled()
  await expect(c.getByTestId('reeval-run-reason')).toHaveText('Claude has no key. Set one in Settings.')

  // Reject asks for a reason first.
  await c.getByRole('button', { name: 'Reject NLLB' }).click()
  await c.getByLabel('Why reject it?', { exact: true }).fill('too literal')
  await c.getByRole('button', { name: 'Reject NLLB' }).click()
  await expect(c.getByTestId('reeval-candidate-status')).toContainText('Rejected NLLB')
  const item = list.locator(':scope > li', { hasText: 'NLLB' })
  await expect(item).toContainText('Rejected')
  await expect(item).toContainText('rejected: too literal')
  await expect(item.getByRole('button', { name: 'Reopen NLLB' })).toBeVisible()

  // Re-adding it shows the recorded decision instead of "added".
  await c.getByRole('button', { name: 'Add candidate' }).click()
  await expect(c.getByTestId('reeval-known')).toContainText(/NLLB: Already evaluated on \d{4}-\d{2}-\d{2}, rejected: too literal/)
  await expect(c.getByTestId('reeval-added')).toHaveCount(0)

  // The decision is in the history.
  await c.locator('summary', { hasText: 'Decision history' }).click()
  await expect(c.getByRole('list', { name: 'Decisions' })).toContainText('too literal')
  // Nothing ran, was promoted or changed the schedule.
  expect(posts.map((p) => p.replace(/\/\d+\//, '/<id>/'))).toEqual([
    '/api/models/reeval/candidates',
    '/api/models/reeval/estimate',
    '/api/models/reeval/candidates/<id>/reject',
    '/api/models/reeval/candidates',
  ])
})

test('estimate, then Run now (confirm: true), progress, and the report against production', async ({ page }) => {
  const { calls, unmocked } = await mockReeval(page, overview())
  await page.goto('/#/benchmark')
  const c = card(page)
  await expect(c.getByTestId('reeval-production')).toContainText('Ollama · qwen3:8b')
  await expect(c.getByTestId('reeval-production')).toContainText('promoted 2026-08-14')
  await expect(c).toContainText('No re-evaluation yet.')

  const run = c.getByRole('button', { name: 'Run now' })
  await expect(run).toBeDisabled()
  await expect(c.getByTestId('reeval-run-reason')).toHaveText('Estimate the cost first.')
  await c.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(c.getByTestId('bench-estimate')).toContainText('Estimate for 12 cases: $0.00')
  await expect(run).toBeEnabled()
  await run.click()

  await expect(c.getByTestId('bench-progress')).toContainText('Running · 50%')
  const rows = c.getByRole('list', { name: 'Candidates against production' })
  await expect(rows.locator(':scope > li').first()).toContainText('Running')
  // Finished: the report reloads with the results.
  await expect(c.getByTestId('bench-progress')).toHaveCount(0, { timeout: 10_000 })
  const first = rows.locator(':scope > li', { hasText: 'qwen2.5:14b' })
  await expect(first).toContainText('+3.4 pts')
  await expect(first).toContainText('+610 ms')
  await expect(first).toContainText('+3.8 GB')
  const second = rows.locator(':scope > li', { hasText: 'NLLB' })
  await expect(second).toContainText('−11.0 pts')
  await expect(second).toContainText('−900 ms')
  await expect(second).toContainText('−5.3 GB')

  const run_ = calls.find((x) => x.path === '/run')
  expect(run_?.body).toEqual({ confirm: true })
  expect(calls.filter((x) => x.method === 'POST').map((x) => x.path)).toEqual(['/estimate', '/run'])

  // Open in Arena: production run first, then the candidate's run.
  const arenaReq = page.waitForRequest((r) => r.url().includes('/api/benchmark/arena'))
  await first.getByRole('button', { name: 'Open Ollama · qwen2.5:14b against production in Arena' }).click()
  expect(new URL((await arenaReq).url()).searchParams.getAll('run_ids')).toEqual(['100', '101'])
  await expect(page.getByRole('region', { name: 'Model Arena' })).toContainText('Baseline')
  expect(unmocked).toEqual([])
})

test('promote needs the second press, sends the reason, and says what changes', async ({ page }) => {
  const { calls, unmocked } = await mockReeval(page, overview({ withReport: true }))
  await page.goto('/#/benchmark')
  const c = card(page)
  const row = c.getByRole('list', { name: 'Candidates against production' }).locator(':scope > li', { hasText: 'NLLB' })
  await expect(row).toContainText("Settings' default engine changes from Ollama to NLLB.")
  await expect(row).toContainText('Presets keep their own model')
  const qwen = c.getByRole('list', { name: 'Candidates against production' }).locator(':scope > li', { hasText: 'qwen2.5:14b' })
  await expect(qwen).toContainText("Settings' default engine stays Ollama.")

  await qwen.getByLabel('Reason for promoting', { exact: true }).fill('better on names')
  await qwen.getByRole('button', { name: 'Promote Ollama · qwen2.5:14b to production' }).click()
  // First press only arms it.
  expect(calls.filter((x) => x.path.endsWith('/promote'))).toEqual([])
  await qwen.getByRole('button', { name: 'Confirm: make Ollama · qwen2.5:14b production' }).click()
  await expect(c.getByTestId('reeval-promote-status')).toHaveText('Ollama · qwen2.5:14b is now the production model.')
  expect(calls.find((x) => x.path.endsWith('/promote'))).toEqual({ method: 'POST', path: '/candidates/1/promote', body: { confirm: true, reason: 'better on names' } })
  await expect(c.getByTestId('reeval-production')).toContainText('Ollama · qwen2.5:14b')
  await expect(c.getByTestId('reeval-production')).toContainText('promoted 2026-09-30')
  await c.locator('summary', { hasText: 'Decision history' }).click()
  await expect(c.getByRole('list', { name: 'Decisions' })).toContainText('better on names')
  await expect(c.getByRole('list', { name: 'Decisions' })).toContainText('Score 84.6% vs production 81.2%')
  expect(unmocked).toEqual([])

  if (SHOTS) {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.reload()
    await card(page).scrollIntoViewIfNeeded()
    await card(page).screenshot({ path: `${SHOTS}/reeval-desktop-after-promote.png` })
  }
})

test('reject asks a reason, reopen puts it back, re-adding a rejected model shows its decision', async ({ page }) => {
  const { calls, unmocked } = await mockReeval(page, overview({ withReport: true }))
  await page.goto('/#/benchmark')
  const c = card(page)
  const list = c.getByRole('list', { name: 'Candidate models' })
  const gemini = list.locator(':scope > li', { hasText: 'Gemini' })
  await expect(gemini).toContainText('Already evaluated on 2026-09-02, rejected: Mangled honorifics in the regression set')

  await c.getByLabel('Candidate engine', { exact: true }).selectOption('gemini')
  await c.getByLabel('Candidate model', { exact: true }).selectOption('gemini-flash-latest')
  await c.getByRole('button', { name: 'Add candidate' }).click()
  await expect(c.getByTestId('reeval-known')).toHaveText(
    'Gemini · gemini-flash-latest: Already evaluated on 2026-09-02, rejected: Mangled honorifics in the regression set',
  )
  expect(calls.find((x) => x.path === '/candidates')?.body).toEqual({ engine: 'gemini', model: 'gemini-flash-latest' })

  await gemini.getByRole('button', { name: 'Reopen Gemini · gemini-flash-latest' }).click()
  await expect(gemini).toContainText('Open')

  const nllbItem = list.locator(':scope > li', { hasText: 'NLLB' })
  await nllbItem.getByRole('button', { name: 'Reject NLLB' }).click()
  await nllbItem.getByLabel('Why reject it?', { exact: true }).fill('worse on idioms')
  await nllbItem.getByRole('button', { name: 'Cancel' }).click()
  expect(calls.filter((x) => x.path.endsWith('/reject'))).toEqual([])
  await nllbItem.getByRole('button', { name: 'Reject NLLB' }).click()
  await nllbItem.getByLabel('Why reject it?', { exact: true }).fill('worse on idioms')
  await nllbItem.getByRole('button', { name: 'Reject NLLB' }).click()
  await expect(nllbItem).toContainText('rejected: worse on idioms')
  expect(calls.find((x) => x.path.endsWith('/reject'))).toEqual({ method: 'POST', path: '/candidates/2/reject', body: { reason: 'worse on idioms' } })
  expect(unmocked).toEqual([])
})

test('schedule: off by default, saves every field with the limit, shows the next due date and a refused attempt', async ({ page }) => {
  const { calls, unmocked } = await mockReeval(page, overview({ withReport: true, error: "Skipped: estimated $0.0400, above the $0.02 limit set for scheduled runs." }))
  await page.goto('/#/benchmark')
  const c = card(page)
  await expect(c.getByTestId('reeval-report-error')).toHaveText(
    'Last scheduled attempt 2026-09-30 03:00 (UTC): Skipped: estimated $0.0400, above the $0.02 limit set for scheduled runs.',
  )
  // The last good report is still shown.
  await expect(c.getByRole('list', { name: 'Candidates against production' }).locator(':scope > li')).toHaveCount(2)

  await c.locator('summary', { hasText: 'Schedule and golden set' }).click()
  const toggle = c.getByRole('switch', { name: 'Re-evaluate on a schedule' })
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
  await expect(c.getByTestId('reeval-next-due')).toHaveText('Schedule off: runs only when you press Run now.')
  const save = c.getByRole('button', { name: 'Save schedule' })
  await expect(save).toBeDisabled()

  await toggle.click()
  await c.getByLabel('Every', { exact: true }).fill('0')
  await expect(c.getByText('Between 1 and 365 days.')).toBeVisible()
  await expect(save).toBeDisabled()
  await c.getByLabel('Every', { exact: true }).fill('14')
  await c.getByLabel('Limit per scheduled run', { exact: true }).fill('0.5')
  await save.click()
  expect(calls.find((x) => x.path === '/settings')?.body).toEqual({
    schedule_enabled: true, interval_days: 14, tier: null, set_name: null, max_cost_usd: 0.5,
  })
  await expect(c.getByTestId('reeval-next-due')).toHaveText('Next run: 2026-10-14 12:00 (UTC).')
  await expect(c.locator('.card-meta')).toContainText('schedule on')
  await expect(c.getByRole('switch', { name: 'Re-evaluate on a schedule' })).toHaveAttribute('aria-checked', 'true')
  await expect(save).toBeDisabled()
  expect(unmocked).toEqual([])

  if (SHOTS) {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.reload()
    await card(page).scrollIntoViewIfNeeded()
    await card(page).screenshot({ path: `${SHOTS}/reeval-desktop.png` })
  }
})

test('off the PC: reads only, no add / reject / promote / save, Run now says why', async ({ page }) => {
  const { calls, unmocked } = await mockReeval(page, overview({ withReport: true }))
  // After mockReeval: its catch-all passes GETs through, and the newest route wins.
  await mockRemote(page)
  await page.goto('/#/benchmark')
  const c = card(page)
  await expect(c.getByRole('list', { name: 'Candidates against production' }).locator(':scope > li')).toHaveCount(2)
  await expect(c).toContainText('Adding, rejecting and promoting candidates is PC only.')
  await expect(c.getByRole('button', { name: 'Add candidate' })).toHaveCount(0)
  await expect(c.getByRole('button', { name: /^Reject / })).toHaveCount(0)
  await expect(c.getByRole('button', { name: /^Reopen / })).toHaveCount(0)
  await expect(c.getByRole('button', { name: /to production$/ })).toHaveCount(0)
  await expect(c.getByTestId('reeval-run-reason')).toHaveText('Starting a run is PC only.')
  // Open in Arena is a read and stays.
  await expect(c.getByRole('button', { name: /in Arena$/ }).first()).toBeVisible()
  await c.locator('summary', { hasText: 'Schedule and golden set' }).click()
  await expect(c.getByRole('switch', { name: 'Re-evaluate on a schedule' })).toBeDisabled()
  await expect(c.getByRole('button', { name: 'Save schedule' })).toHaveCount(0)
  await expect(c).toContainText('Run this on the main PC.')
  expect(calls.filter((x) => x.method !== 'GET')).toEqual([])
  expect(unmocked).toEqual([])
})
