import type { Page } from '@playwright/test'

// Mocks for Review > Re-time with Qwen3 aligner on drama 3: no aligner or GPU in
// e2e (pytest covers the real routes), so the job, its polling, the proposals
// and the apply route are faked.

export const RETIME_PROPOSALS = [
  { line_id: 11, number: 2, base_zh: '第1句', start: 2, end: 3.5, new_start: 1.6, new_end: 3.4, uncertain: false },
  { line_id: 12, number: 5, base_zh: '第4句', start: 8, end: 9.5, new_start: 7.75, new_end: 9.5, uncertain: true },
]

const job = (status: string, extra: Record<string, unknown> = {}) => ({
  job_id: 'retime_3', status, progress: status === 'running' ? 0.5 : 1,
  message: status === 'running' ? 'Aligning on GPU...' : '', error: null, description: null, gpu_touching: true,
  started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: 1, ...extra,
})

export interface RetimeSeen {
  runs: unknown[]
  applies: unknown[]
  // With `hold`, the job reports running until this is called.
  release: () => void
}

// `hold` keeps the job running until `release()`: a test that asserts the progress line
// must see it before the next poll finishes the job, which a slow runner can miss.
export async function mockRetime(page: Page, { hold = false } = {}): Promise<RetimeSeen> {
  let held = hold
  const seen: RetimeSeen = { runs: [], applies: [], release: () => { held = false } }
  const base = '**/api/transcribe/dramas/3/retime'
  let polls = 0
  await page.route(`${base}/run`, (r) => {
    seen.runs.push(r.request().postDataJSON())
    return r.fulfill({ json: { job_id: 'retime_3', drama_id: 3, line_count: 3 } })
  })
  await page.route('**/api/jobs/retime_3', (r) => {
    polls += 1
    return r.fulfill({ json: held || polls < 2 ? job('running') : job('done', { outcome: 'ok', result: { line_count: 3, candidate_count: 2 } }) })
  })
  await page.route(`${base}/result`, (r) =>
    r.fulfill({ json: { job_id: 'retime_3', proposals: RETIME_PROPOSALS, line_count: 3, partial: false, device: 'CPU',
      device_notice: 'Re-timing with the Qwen3 aligner ran on the CPU because the GPU couldn’t be used. This was slower than on the GPU.',
      errors: ['line 9: no text to align, skipped'] } }))
  await page.route(`${base}/apply`, (r) => {
    const body = r.request().postDataJSON() as { items: { line_id: number }[] }
    seen.applies.push(body)
    return r.fulfill({ json: { applied: body.items.map((i) => i.line_id), skipped: [], overlapping: [] } })
  })
  return seen
}
