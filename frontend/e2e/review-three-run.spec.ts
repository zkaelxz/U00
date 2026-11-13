import { expect, test } from '@playwright/test'

import { openFoldFor } from './reviewFolds'

// Optional "Run 3 times, review differences" in Review's tools: confirm with an
// estimate, three ordinary translate runs, then only the lines the runs differ on,
// picked with the keys 1/2/3. Every write is answered here, never sent.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const line = (id: number) => ({
  id, idx: id - 1, start: id, end: id + 1, zh: `句${id}`, en: `Line ${id}`, speaker: null,
  speaker_manual: false, sfx: false, flag: null, flag_note: '', dub_filename: null, lang: null,
})

test('three runs, then pick only the differing lines by key', async ({ page }) => {
  const seen = { begin: 0, runs: 0, captures: [] as number[], picks: [] as unknown[], keepRest: [] as unknown[] }
  let state: Record<string, unknown> = { active: false, runs_done: 0, history_id: null, line_count: 0, items: [] }
  const items = [
    { line_id: 1, idx: 0, zh: '句1', versions: ['A1', 'A2', 'A3'], picked_run: null, current_en: 'A3' },
    { line_id: 2, idx: 1, zh: '句2', versions: ['B1', 'B2', 'B3'], picked_run: null, current_en: 'B3' },
  ]
  await page.route('**/api/**', (route) => (route.request().method() === 'GET' ? route.continue() : route.abort()))
  await page.route('**/api/review/dramas/3/lines?*', (route) =>
    route.fulfill({ json: { lines: [1, 2, 3].map(line), page: 1, page_size: 40, total: 3, flagged_count: 0, untranslated_count: 0 } }))
  await page.route('**/api/three-run/dramas/3', (route) => route.fulfill({ json: state }))
  await page.route('**/api/three-run/dramas/3/begin', (route) => {
    seen.begin += 1
    state = { ...state, active: true, history_id: 9, line_count: 3 }
    return route.fulfill({ json: { line_ids: [1, 2, 3], history_id: 9, runs_done: 0 } })
  })
  await page.route('**/api/translate-run/dramas/3/run', (route) => {
    seen.runs += 1
    return route.fulfill({
      json: { job_id: 'translate_3', drama_id: 3, engine: 'fake', model: null, target_line_count: 3, fallback_engines: [], reflect: false, bulk: false, thinking: false, line_ids: [1, 2, 3] },
    })
  })
  await page.route('**/api/jobs/translate_3', (route) =>
    route.fulfill({
      json: { job_id: 'translate_3', status: 'done', progress: 1, message: 'Done', error: null, description: null, gpu_touching: false, started_at: 1, finished_at: 2, updated_at: 2 },
    }))
  await page.route('**/api/three-run/dramas/3/capture', (route) => {
    const run = (route.request().postDataJSON() as { run: number }).run
    seen.captures.push(run)
    state = { ...state, runs_done: run, items: run === 3 ? items : [] }
    return route.fulfill({ json: state })
  })
  await page.route('**/api/three-run/dramas/3/pick', (route) => {
    const body = route.request().postDataJSON() as { picks: { line_id: number; run: number }[] }
    seen.picks.push(body)
    for (const p of body.picks) items.find((i) => i.line_id === p.line_id)!.picked_run = p.run
    return route.fulfill({ json: { applied: body.picks.map((p) => p.line_id), skipped: [] } })
  })
  await page.route('**/api/three-run/dramas/3/keep-rest', (route) => {
    seen.keepRest.push(route.request().postDataJSON())
    return route.fulfill({ json: { applied: [2], skipped: [] } })
  })

  await page.goto('/#/drama/3/review')
  await openFoldFor(page, 'Run 3 times, review differences')
  const panel = page.getByTestId('three-run')
  await panel.scrollIntoViewIfNeeded()
  await panel.getByRole('button', { name: 'Run 3 times…' }).click()
  expect(seen.begin).toBe(0) // asks to confirm first
  await panel.getByTestId('three-run-confirm').click()

  await expect(page.getByTestId('three-run-left')).toContainText('2 of 2 differing lines left')
  expect(seen.captures).toEqual([1, 2, 3])
  expect(seen.runs).toBe(3)

  await page.getByTestId('three-run-review').focus()
  await page.keyboard.press('2')
  await expect.poll(() => seen.picks.length).toBe(1)
  expect(seen.picks[0]).toEqual({ picks: [{ line_id: 1, run: 2 }] })
  await expect(page.getByTestId('three-run-left')).toContainText('1 of 2 differing lines left')

  await page.getByTestId('three-run-keep-1').click()
  await expect.poll(() => seen.keepRest).toEqual([{ run: 1 }])
})
