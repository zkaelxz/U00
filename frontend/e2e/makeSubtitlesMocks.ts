import type { Page, Request } from '@playwright/test'

import { ME } from './authMocks'
import { STATS } from './getStartedMocks'

// The Make subtitles flow with nothing real behind it: create, upload, the two
// jobs, translate config/run and the SRT export are fulfilled here. Jobs read as
// running until `release()`, then done (or error for `failJob`). Preflight is all OK.

export const DRAMA_ID = 99
export const SRT = '1\n00:00:01,000 --> 00:00:02,000\nHello.\n'

const job = (id: string, status: 'running' | 'done' | 'error') => ({
  job_id: id, status, progress: status === 'running' ? 0.4 : 1, message: status === 'running' ? 'working' : 'finished',
  error: status === 'error' ? 'The translator refused the request.' : null, description: null, gpu_touching: false,
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
  o: { libraryTotal?: number; failJob?: string; holdFirst?: boolean } = {},
): Promise<MakeSubtitlesMock> {
  const m: MakeSubtitlesMock = { created: [], uploads: [], translateRuns: [], release: () => { held = false } }
  let held = o.holdFirst ?? false
  const started = new Set<string>()
  const json = (route: import('@playwright/test').Route, body: unknown, status = 200) => route.fulfill({ status, json: body })

  await page.route('**/api/events?*', (r) => json(r, { error: { code: 'rate_limited', message: 'No stream.' } }, 429))
  await page.route((u) => u.pathname === '/api/auth/me', (r) => json(r, ME.authOff))
  await page.route('**/api/library/stats', (r) => json(r, STATS(o.libraryTotal ?? 1)))
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
    started.add('transcribe')
    return json(r, { upload: { name: 'a.mp3', size: 3, kind: 'audio', job_id: null }, job_id: `transcribe_${DRAMA_ID}` })
  })
  // The real config for drama 1 stands in for the new title's (same shape, defaults).
  await page.route(`**/api/translate-run/dramas/${DRAMA_ID}/config`, async (r) => {
    const real = await r.fetch({ url: '/api/translate-run/dramas/1/config' })
    await r.fulfill({ response: real })
  })
  await page.route(`**/api/translate-run/dramas/${DRAMA_ID}/run`, (r) => {
    m.translateRuns.push(r.request().postDataJSON())
    started.add('translate')
    return json(r, { job_id: `translate_${DRAMA_ID}`, drama_id: DRAMA_ID, engine: 'deepseek', model: null, target_line_count: 1, fallback_engines: [] })
  })
  await page.route(/\/api\/jobs\/(transcribe|translate)_99$/, (r) => {
    const id = new URL(r.request().url()).pathname.split('/').pop()!
    const kind = id.split('_')[0]
    if (!started.has(kind)) return json(r, { error: { code: 'not_found', message: 'No job.' } }, 404)
    if (held && kind === 'transcribe') return json(r, job(id, 'running'))
    return json(r, job(id, o.failJob === kind ? 'error' : 'done'))
  })
  await page.route(/\/api\/jobs\/extract_audio_99$/, (r) => json(r, { error: { code: 'not_found', message: 'No job.' } }, 404))
  await page.route(`**/api/export/dramas/${DRAMA_ID}/subtitle?*`, (r) => r.fulfill({ status: 200, contentType: 'text/plain', body: SRT }))
  await page.route(`**/api/export/dramas/${DRAMA_ID}/mark-exported`, (r) => json(r, { drama_id: DRAMA_ID, status: 'exported' }))
  return m
}
