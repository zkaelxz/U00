import type { Page } from '@playwright/test'

// Mocks for Review > Compare transcription on drama 3: no Whisper, GPU or
// engine in e2e (pytest covers the real routes), so options, estimate, the
// job, its polling, the proposals and the apply route are all faked.

export const OPTIONS = {
  has_audio: true, no_audio_reason: null, max_lines: 200,
  saved_whisper_size: 'small', saved_asr_backend: 'whisper', saved_alignment_method: 'whisper_diff',
  whisper_sizes: ['base', 'large-v3', 'medium', 'small', 'tiny'],
  backends: [
    { id: 'whisper', label: 'Whisper', available: true, reason: null },
    { id: 'qwen3_asr', label: 'Qwen3 ASR', available: false, reason: 'Qwen3-ASR needs torch, which isn’t installed yet. Open Diagnostics to install it.' },
    { id: 'qwen3_asr_vad', label: 'Qwen3 ASR with speech detection', available: false, reason: 'Qwen3-ASR needs torch, which isn’t installed yet. Open Diagnostics to install it.' },
    { id: 'qwen3_asr_long', label: 'Qwen3 ASR on long windows', available: false, reason: 'Qwen3-ASR needs torch, which isn’t installed yet. Open Diagnostics to install it.' },
  ],
  translation_engine: 'claude',
}

export const PROPOSALS = [
  { line_id: 11, number: 1, start: 0, end: 1.5, base_zh: '魏婴来了', base_en: 'Wei Ying is here', candidate_zh: '魏婴来啦', current_en: 'Wei Ying is here', candidate_en: 'Wei Ying has arrived', translated: true },
  { line_id: 12, number: 2, start: 1.5, end: 3, base_zh: '你好', base_en: '', candidate_zh: '你好', current_en: '', candidate_en: 'Hello', translated: true },
  { line_id: 13, number: 3, start: 3, end: 4, base_zh: '再见', base_en: 'Bye', candidate_zh: '在见', current_en: 'Bye', candidate_en: 'See it', translated: true },
]

const job = (status: string, extra: Record<string, unknown> = {}) => ({
  job_id: 'comparetx_3', status, progress: status === 'running' ? 0.5 : 1,
  message: status === 'running' ? 'Line 2 of 3' : '', error: null, description: null, gpu_touching: true,
  started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: 1, ...extra,
})

export interface CompareSeen {
  estimates: unknown[]
  runs: unknown[]
  applies: unknown[]
}

export async function mockCompare(page: Page, opts: { estimate?: Record<string, unknown> } = {}): Promise<CompareSeen> {
  const seen: CompareSeen = { estimates: [], runs: [], applies: [] }
  const base = '**/api/transcribe/dramas/3/compare-transcription'
  let polls = 0
  await page.route(`${base}/options`, (r) => r.fulfill({ json: OPTIONS }))
  await page.route(`${base}/estimate`, (r) => {
    seen.estimates.push(r.request().postDataJSON())
    return r.fulfill({ json: { line_count: 3, max_lines: 200, translate: true, estimated_usd: 0.04, free: false, cap_applies: true, effective_cap_usd: 5, monthly_refusal: false, estimate_above_cap: false, ...opts.estimate } })
  })
  await page.route(`${base}/run`, (r) => {
    seen.runs.push(r.request().postDataJSON())
    return r.fulfill({ json: { job_id: 'comparetx_3', drama_id: 3, line_count: 3 } })
  })
  await page.route('**/api/jobs/comparetx_3', (r) => {
    polls += 1
    return r.fulfill({ json: polls < 2 ? job('running') : job('done', { outcome: 'ok', result: { line_count: 3, candidate_count: 3 } }) })
  })
  await page.route(`${base}/result`, (r) =>
    r.fulfill({ json: { job_id: 'comparetx_3', proposals: PROPOSALS, line_count: 3, asr_backend: 'whisper', whisper_size: 'tiny', translated: true, partial: false, cap_reached: false, errors: [] } }))
  await page.route(`${base}/apply`, (r) => {
    const body = r.request().postDataJSON() as { items: { line_id: number }[] }
    seen.applies.push(body)
    return r.fulfill({ json: { applied: body.items.map((i) => i.line_id), skipped: [] } })
  })
  return seen
}
