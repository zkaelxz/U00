import type { Page } from '@playwright/test'

// Mocks for Review > Re-transcribe selected and "Transcribe this gap" on drama 3:
// no Whisper or GPU in e2e (pytest covers the real routes), so the job, its
// polling, the proposals and the apply route are faked.

const job = (status: string, extra: Record<string, unknown> = {}) => ({
  job_id: 'retranscribe_3', status, progress: status === 'running' ? 0.5 : 1,
  message: status === 'running' ? 'Heard line 1 of 3' : '', error: null, description: null, gpu_touching: true,
  started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: 1, ...extra,
})

export interface RetranscribeSeen {
  starts: unknown[]
  applies: unknown[]
  cancels: number
  // With `hold`, the job reports running until this is called.
  release: () => void
}

export type Proposal = { line_id: number; number: number; base_zh: string; proposed_zh: string; had_english: boolean }

export async function mockRetranscribeLines(
  page: Page,
  proposals: Proposal[],
  { hold = false, failures = [] as unknown[] } = {},
): Promise<RetranscribeSeen> {
  let held = hold
  const seen: RetranscribeSeen = { starts: [], applies: [], cancels: 0, release: () => { held = false } }
  const base = '**/api/transcribe/dramas/3/retranscribe-lines'
  await page.route('**/api/transcribe/dramas/3/config', (r) =>
    r.fulfill({ json: { drama_id: 3, has_audio_pipeline: true, audio_available: true } }))
  let shown = proposals
  await page.route(`${base}`, (r) => {
    if (r.request().method() === 'POST') {
      const body = r.request().postDataJSON() as { line_ids: number[] }
      seen.starts.push(body)
      return r.fulfill({ json: { job_id: 'retranscribe_3', drama_id: 3, line_count: body.line_ids.length } })
    }
    return r.fulfill({
      json: {
        job_id: 'retranscribe_3', line_count: proposals.length + failures.length, proposals: shown, failures,
        unchanged_count: 0, truncated: false, device_notice: null,
      },
    })
  })
  await page.route(`${base}/apply`, (r) => {
    const body = r.request().postDataJSON() as { items: { line_id: number }[] }
    seen.applies.push(body)
    const applied = body.items.map((i) => i.line_id)
    shown = shown.filter((p) => !applied.includes(p.line_id))
    return r.fulfill({ json: { applied, skipped: [], untranslated_count: applied.length } })
  })
  await page.route('**/api/jobs/retranscribe_3/cancel', (r) => {
    seen.cancels += 1
    return r.fulfill({ json: { job_id: 'retranscribe_3', cancel_requested: true } })
  })
  await page.route('**/api/jobs/retranscribe_3', (r) =>
    r.fulfill({
      json: held
        ? job('running', { result: { line_count: proposals.length } })
        : job('done', { result: { line_count: proposals.length, candidate_count: proposals.length }, outcome: 'ok' }),
    }))
  return seen
}
