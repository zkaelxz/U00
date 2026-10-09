import type { Page, Route } from '@playwright/test'
import { REMOTE_HEALTH_OFF } from './authMocks'

// Shared page.route mocks for the Live specs. A real session would run
// yt-dlp, ffmpeg and Whisper, so every /api call the page makes is mocked
// here; anything else under /api is aborted and recorded.

export const SCREENS = process.env.E2E_SCREENS ?? 'test-results/screens/live'

export const SID = `live_${'1'.repeat(32)}`

const ENGINES = [
  { name: 'deepseek', label: 'DeepSeek -- cheap', free: false, models: null, key_configured: true },
  { name: 'claude', label: 'Claude', free: false, models: null, key_configured: false },
  { name: 'fake', label: 'Fake', free: true, models: null, key_configured: true },
]

const OLLAMA = {
  name: 'ollama', label: 'Ollama', free: true, key_configured: true,
  models: ['qwen3:8b', 'gemma4:12b'], model_labels: {},
}

export const cue = (n: number) => ({
  id: n, translation: 'done' as 'done' | 'pending' | 'failed' | 'cancelled', start: n * 20, end: n * 20 + 4, text: `第${n}句台词，内容比较长一些以便测试换行效果。`,
  translated: `Line ${n}: a longer English translation so the phone layout has to wrap it.`,
})

/** A line whose transcript is shown and whose translation has not arrived. */
export const pendingCue = (n: number) => ({ ...cue(n), translated: '', translation: 'pending' as const })

interface LiveMocks {
  ollamaChecks: string[]
  posts: { url: string; body: unknown; headers: Record<string, string> }[]
  polls: string[]
  unmocked: string[]
  state: {
    sessions: { session_id: string; status: string; engine: string | null; cue_count: number }[]
    status: string
    message: string
    cues: ReturnType<typeof cue>[]
    startStatus: number
    model: string | null
    notes: string[]
  }
}

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })

export async function mockLive(page: Page, opts: { remote?: boolean; ollama?: boolean; enginesFail?: boolean; ollamaMissing?: boolean } = {}): Promise<LiveMocks> {
  const m: LiveMocks = {
    posts: [], polls: [], unmocked: [], ollamaChecks: [],
    state: { sessions: [], status: 'queued', message: 'Waiting for the GPU', cues: [], startStatus: 200, model: null, notes: [] },
  }
  // Registered first, so it only answers what nothing below handles.
  await page.route('**/api/**', (route) => {
    const r = route.request()
    m.unmocked.push(`${r.method()} ${new URL(r.url()).pathname}`)
    return route.abort()
  })
  await page.route('**/api/meta', (route) =>
    json(route, { app: 'baihe', api_version: '1', environment: 'development', local: !opts.remote }))
  await page.route('**/api/auth/me', (route) => json(route, {
    auth_enabled: false, signed_in: true, sign_in_configured: false, zone: 'pc',
    user: { id: null, email: null, display_name: 'This PC', is_admin: true, is_local_owner: true }, permissions: [],
  }))
  // The header bell (every page) polls this; not part of the Live flow.
  await page.route('**/api/notifications', (route) => json(route, { items: [] }))
  // The header Jobs button (every page) reads this; not part of the Live flow.
  await page.route((u) => u.pathname === '/api/jobs', (route) => json(route, { items: [] }))
  // The app shell's remote-access banner (PC only): remote access off.
  await page.route('**/api/diagnostics/remote-health', (route) => json(route, REMOTE_HEALTH_OFF))
  // No push stream: these specs drive the page through its polling fallback
  // (event-stream.spec.ts covers the pushed Live status).
  await page.route('**/api/events?*', (route) =>
    json(route, { error: { code: 'rate_limited', message: 'No stream in this test.' } }, 429))
  await page.route('**/api/translate/engines', (route) => opts.enginesFail
    ? json(route, { error: { code: 'internal', message: 'Boom.' } }, 500)
    : json(route, { items: opts.ollama ? [OLLAMA, ...ENGINES] : ENGINES }))
  await page.route('**/api/live/ollama-check*', (route) => {
    const model = new URL(route.request().url()).searchParams.get('model') ?? ''
    m.ollamaChecks.push(model)
    return json(route, opts.ollamaMissing
      ? { ok: false, model, message: `Ollama doesn't have the model ${model}. Run "ollama pull ${model}" first, or pick another model in Settings.` }
      : { ok: true, model, message: null })
  })
  // The header asks whether to show the Assistant link (Developer Mode off).
  await page.route('**/api/assistant/settings', (route) => json(route, { developer_mode: false, engine: null, model: null, engine_choices: [] }))
  await page.route('**/api/live/sessions', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return json(route, m.state.sessions)
    m.posts.push({ url: r.url(), body: r.postDataJSON(), headers: r.headers() })
    if (m.state.startStatus !== 200) {
      return json(route, { error: { code: 'forbidden', message: 'Not allowed.' } }, m.state.startStatus)
    }
    m.state.sessions = [{ session_id: SID, status: 'queued', engine: 'deepseek', cue_count: 0 }]
    return json(route, { session_id: SID })
  })
  await page.route(`**/api/live/sessions/${SID}?*`, (route) => {
    const after = Number(new URL(route.request().url()).searchParams.get('after') ?? 0)
    m.polls.push(`after=${after}`)
    const { status, message, cues } = m.state
    return json(route, {
      session_id: SID, status, message, model: m.state.model, progress: 0, notes: m.state.notes, cues: cues.slice(after), next_index: Math.max(after, cues.length),
    })
  })
  await page.route(`**/api/live/sessions/${SID}/stop`, (route) => {
    const r = route.request()
    m.posts.push({ url: r.url(), body: null, headers: r.headers() })
    m.state.status = 'cancelled'
    m.state.message = 'Cancelled.'
    return json(route, { session_id: SID, stopping: true })
  })
  return m
}

export async function openLive(page: Page) {
  await page.goto('/#/live')
  return page.getByRole('region', { name: 'Live' })
}
