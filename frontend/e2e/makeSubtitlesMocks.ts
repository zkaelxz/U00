import type { Page, Request } from '@playwright/test'

import { ME } from './authMocks'
import { json } from './mockHelpers'

// The Add page's file run with nothing real behind it: create, upload, the two
// jobs, the queue's file run and the SRT export are fulfilled here. Jobs read as
// running until `release()`, then done (or error for `failJob`). Preflight is all OK.

export const DRAMA_ID = 99
export const NO_LINES = 'Finished but found no lines. Possible reasons: the audio is silent.'
export const SRT = '1\n00:00:01,000 --> 00:00:02,000\nHello.\n'

const job = (id: string, status: 'running' | 'done' | 'error') => ({
  job_id: id, status, progress: status === 'running' ? 0.4 : 1, message: status === 'running' ? 'working' : 'finished',
  error: status === 'error' ? 'RuntimeError: The translator refused the request.' : null, description: null, gpu_touching: false,
  started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: Date.now() / 1000,
  outcome: status === 'done' ? 'ok' : status === 'error' ? 'failed' : null,
})

export interface MakeSubtitlesMock {
  created: unknown[]
  uploads: Request[]
  translateRuns: unknown[]
  /** Jobs the page asked for that the mock was never told about. */
  release: () => void
}

export async function mockMakeSubtitles(
  page: Page,
  o: { failJob?: string; holdFirst?: boolean; holdUpload?: boolean; cancelledJob?: boolean; noLines?: boolean } = {},
): Promise<MakeSubtitlesMock> {
  const m: MakeSubtitlesMock = { created: [], uploads: [], translateRuns: [], release: () => { held = false } }
  let held = o.holdFirst ?? false
  const started = new Set<string>()

  await page.route('**/api/events?*', (r) => json(r, { error: { code: 'rate_limited', message: 'No stream.' } }, 429))
  await page.route((u) => u.pathname === '/api/auth/me', (r) => json(r, { ...ME.authOff, permissions: [...ME.authOff.permissions, 'library.read', 'library.create'] }))
  await page.route('**/api/translate/engines', (r) => json(r, {
    items: [
      { name: 'deepseek', label: 'DeepSeek.', free: false, models: null, key_configured: true },
      { name: 'claude', label: 'Claude.', free: false, models: null, key_configured: false },
    ],
    default_engine: 'deepseek',
  }))
  await page.route('**/api/diagnostics/install-presets', (r) => json(r, {
    tasks: [], packages: { faster_whisper: { name: 'faster_whisper', dist: 'faster-whisper', installed: true, installable: false, powers: '', approx_mb: 80, pulls_torch: false, source_url: '', not_offered_reason: null, warning: null } },
  }))
  await page.route('**/api/diagnostics/setup-checks', (r) => json(r, {
    python: { version: '3.12.4', ok: true }, ffmpeg: { found: true, version: '6.1' }, js_runtime: { found: true, name: 'deno' },
    cuda: { torch_installed: false, cuda_available: null }, files: { all_present: true, missing_top_level: [] }, library_writable: true,
  }))
  await page.route((u) => u.pathname === '/api/jobs', (r) => json(r, { items: [], count: 0 }))

  await page.route((u) => u.pathname === '/api/dramas', (r) => {
    if (r.request().method() !== 'POST') return r.fallback()
    m.created.push(r.request().postDataJSON())
    return json(r, { id: DRAMA_ID, title_en: 'x', status: 'new' })
  })
  await page.route(`**/api/media/dramas/${DRAMA_ID}/upload-and-transcribe`, (r) => {
    m.uploads.push(r.request())
    if (o.holdUpload) return new Promise<void>(() => undefined) // the page aborts it
    started.add('transcribe')
    return json(r, { upload: { name: 'a.mp3', size: 3, kind: 'audio', job_id: null }, job_id: `transcribe_${DRAMA_ID}` })
  })
  // The run on the PC queue: it follows the upload's transcription, then starts the translation itself.
  let posted: unknown = null
  let translateRead = false
  await page.route(`**/api/queue/file-run/${DRAMA_ID}`, (r) => {
    const idle = { state: 'idle', step: null, reason: null, job_id: null }
    if (r.request().method() === 'POST') {
      posted = r.request().postDataJSON()
      translateRead = false
      return json(r, { state: 'running', step: 'follow', reason: null, job_id: `transcribe_${DRAMA_ID}` })
    }
    if (!posted) return json(r, idle)
    if (held) return json(r, { state: 'running', step: 'follow', reason: null, job_id: `transcribe_${DRAMA_ID}` })
    if (!translateRead) {
      translateRead = true
      started.add('translate')
      m.translateRuns.push(posted)
      return json(r, { state: 'running', step: 'run_translate', reason: null, job_id: `translate_${DRAMA_ID}` })
    }
    return json(r, o.failJob === 'translate'
      ? { state: 'stopped', step: 'run_translate', reason: 'The translator refused the request.', job_id: `translate_${DRAMA_ID}` }
      : { state: 'done', step: null, reason: null, job_id: `translate_${DRAMA_ID}` })
  })
  await page.route(/\/api\/jobs\/(transcribe|translate)_99$/, (r) => {
    const id = new URL(r.request().url()).pathname.split('/').pop()!
    const kind = id.split('_')[0]
    if (!started.has(kind)) return json(r, { error: { code: 'not_found', message: 'No job.' } }, 404)
    if (held && kind === 'transcribe') return json(r, job(id, 'running'))
    if (o.cancelledJob && kind === 'transcribe') return json(r, { ...job(id, 'done'), status: 'cancelled', outcome: 'cancelled' })
    if (o.noLines && kind === 'transcribe') {
      return json(r, { ...job(id, 'done'), outcome: 'partial', outcome_message: NO_LINES, result: { failed_reason: 'no_lines' } })
    }
    return json(r, job(id, o.failJob === kind ? 'error' : 'done'))
  })
  await page.route(/\/api\/jobs\/extract_audio_99$/, (r) => json(r, { error: { code: 'not_found', message: 'No job.' } }, 404))
  await page.route(`**/api/export/dramas/${DRAMA_ID}/subtitle?*`, (r) => r.fulfill({ status: 200, contentType: 'text/plain', body: SRT }))
  await page.route(`**/api/export/dramas/${DRAMA_ID}/mark-exported`, (r) => json(r, { drama_id: DRAMA_ID, status: 'exported' }))
  return m
}
