import type { Page } from '@playwright/test'

import { clearLines, seedLines } from './resegmentLlmMocks'

export { clearLines, seedLines }

export const SUMMARY = {
  split_lines: 31, lines_before: 260, line_count: 347, timing: 'proportional', aligned_lines: 0,
  cleared_translations: 0, speakers_reassigned: true, note: '',
}

interface Calls {
  resplits: Record<string, unknown>[]
  reassigns: number
}

/** Mocks the re-split route (a `reply` per call), the reassign route and the resplit job. */
export async function mockResplit(
  page: Page,
  reply: (body: Record<string, unknown>, n: number) => { status?: number; json: unknown },
  jobResult: Record<string, unknown> = SUMMARY,
): Promise<Calls> {
  const calls: Calls = { resplits: [], reassigns: 0 }
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
  const group = page.getByRole('group', { name: 'Re-split long lines' })
  await group.locator('summary', { hasText: 'Re-split long lines' }).click()
  return group
}
