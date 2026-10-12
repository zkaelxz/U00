import type { Page } from '@playwright/test'

import { clearLines, seedLines, setSourceLanguage } from './resegmentLlmMocks'
import { openFoldFor } from './reviewFolds'

export { clearLines, seedLines, setSourceLanguage }

export const SUMMARY = {
  split_lines: 31, lines_before: 260, line_count: 347, timing: 'proportional', aligned_lines: 0,
  cleared_translations: 0, speakers_reassigned: true, note: '',
}

interface Calls {
  resplits: Record<string, unknown>[]
  reassigns: number
}

/** The install-presets answer the "More" gate reads: jieba and the Japanese splitter installed or not. */
export async function mockSplitters(page: Page, installed: boolean) {
  const pkg = (name: string) => [name, { name, dist: name, installed, installable: true, powers: '', approx_mb: 20, pulls_torch: false, source_url: null, not_offered_reason: null }]
  await page.route('**/api/diagnostics/install-presets', (route) =>
    route.fulfill({ json: { tasks: [], packages: Object.fromEntries(['jieba', 'sudachipy', 'sudachidict_core'].map(pkg)) } }),
  )
}

/** Mocks the re-split route (a `reply` per call), the reassign route and the resplit job. */
export async function mockResplit(
  page: Page,
  reply: (body: Record<string, unknown>, n: number) => { status?: number; json: unknown },
  jobResult: Record<string, unknown> = SUMMARY,
): Promise<Calls> {
  const calls: Calls = { resplits: [], reassigns: 0 }
  await mockSplitters(page, true)
  await page.route('**/api/restructure/dramas/3/resplit', (route) => {
    const body = route.request().postDataJSON()
    calls.resplits.push(body)
    return route.fulfill(reply(body, calls.resplits.length))
  })
  await page.route('**/api/diarization/dramas/3/reassign', (route) => {
    calls.reassigns += 1
    return route.fulfill({ json: { changed: 12, kept_manual: 2 } })
  })
  await page.route('**/api/jobs/resplit_3', (route) =>
    route.fulfill({
      json: {
        job_id: 'resplit_3', status: 'done', progress: null, message: '', error: null, description: null,
        gpu_touching: true, started_at: 1, finished_at: 2, updated_at: 1, outcome: 'ok', result: jobResult,
      },
    }),
  )
  return calls
}

export async function openResplit(page: Page) {
  await page.goto('/#/drama/3/review')
  await page.locator('.review-line:not(.review-skeleton)').first().waitFor()
  await openFoldFor(page, 'Re-split long lines')
  const group = page.getByRole('group', { name: 'Re-split long lines' })
  return group
}
