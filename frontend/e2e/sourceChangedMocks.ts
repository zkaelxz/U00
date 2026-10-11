import type { Page } from '@playwright/test'

// Drama 3 with two lines whose source changed after translation (and one
// plain line). Every write is captured and answered here, never sent, so the
// shared e2e library is not touched; other GETs reach the seeded server.

const line = (id: number, flag: string | null) => ({
  id, idx: id - 1, start: id, end: id + 1, zh: `句${id}`, en: `Line ${id}`, speaker: null,
  speaker_manual: false, sfx: false, flag, flag_note: '', dub_filename: null, lang: null,
})

export interface SourceChangedSeen {
  keeps: unknown[]
  runs: Record<string, unknown>[]
}

export async function mockSourceChanged(page: Page): Promise<SourceChangedSeen> {
  const seen: SourceChangedSeen = { keeps: [], runs: [] }
  let flagged = [1, 3]
  const lines = () => [1, 2, 3].map((id) => line(id, flagged.includes(id) ? 'source_changed' : null))
  await page.route('**/api/**', (route) => (route.request().method() === 'GET' ? route.continue() : route.abort()))
  await page.route('**/api/review/dramas/3/lines?*', (route) => {
    const only = new URL(route.request().url()).searchParams.get('only')
    const shown = only === 'flagged' ? lines().filter((l) => l.flag) : lines()
    return route.fulfill({
      json: { lines: shown, page: 1, page_size: 40, total: shown.length, flagged_count: flagged.length, untranslated_count: 0 },
    })
  })
  await page.route('**/api/review/dramas/3/source-changed', (route) =>
    route.fulfill({ json: { count: flagged.length, line_ids: flagged } }))
  await page.route('**/api/lines/dramas/3/source-changed/keep-english', (route) => {
    const body = route.request().postDataJSON() as { line_ids: number[] }
    seen.keeps.push(body)
    const cleared = flagged.filter((id) => body.line_ids.includes(id))
    flagged = flagged.filter((id) => !cleared.includes(id))
    return route.fulfill({ json: { cleared: cleared.length, line_ids: cleared, skipped_ids: [] } })
  })
  await page.route('**/api/translate-run/dramas/3/source-changed/run', (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown> & { line_ids: number[] }
    seen.runs.push(body)
    return route.fulfill({
      json: {
        job_id: 'translate_3', drama_id: 3, engine: 'fake', model: null, target_line_count: body.line_ids.length,
        fallback_engines: [], reflect: false, bulk: false, thinking: false, line_ids: body.line_ids,
      },
    })
  })
  await page.route('**/api/jobs/translate_3', (route) =>
    route.fulfill({
      json: {
        job_id: 'translate_3', status: 'running', progress: 0.5, message: 'Batch 1 of 1', error: null,
        description: null, gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1,
      },
    }))
  return seen
}
