import { expect, type Locator, type Page, type Route } from '@playwright/test'
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

export interface FollowRow {
  id: number
  channel: string
  channel_name: string
  mode: 'notify' | 'auto'
  auto_check: boolean
  options: Record<string, unknown>
  poll_minutes: number
  last_checked_at: number | null
  state: string
  last_status: string
  starts_at: number | null
  live_now: boolean
  stream_title: string
  checking: boolean
  mine: boolean
  created_at: number
}

const FOLLOW_OPTIONS = { source_language: 'zh', whisper_size: 'small', engine: null, model: null, max_minutes: 60, use_gpu: false, glossary_drama_id: null, series_id: null }

/** A followed channel as GET /api/live/follows lists it. */
export const followRow = (id: number, over: Partial<FollowRow> = {}): FollowRow => ({
  id, channel: `@channel${id}`, channel_name: `Channel ${id}`, mode: 'notify', auto_check: false, options: { ...FOLLOW_OPTIONS },
  poll_minutes: 60, last_checked_at: null, state: '', last_status: '', starts_at: null, live_now: false,
  stream_title: '', checking: false, mine: true, created_at: 1_800_000_000, ...over,
})

interface LiveMocks {
  /** Followed channels (the card): the list served, and every POST under /api/live/follows. */
  follows: { items: FollowRow[]; can_auto_start: boolean; posts: { path: string; body: unknown }[]; addStatus: number }
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
    /** GET /api/live/sessions answers a server fault. */
    sessionsFail: boolean
    model: string | null
    notes: string[]
    /** Lag figures added to the status (lag_seconds, processing_seconds, ...). */
    figures: Record<string, number | boolean>
    /** The first Stop is taken as "finish": the session keeps running until a second one. */
    finishOnStop: boolean
  }
}

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })

export async function mockLive(page: Page, opts: { remote?: boolean; ollama?: boolean; enginesFail?: boolean; ollamaMissing?: boolean } = {}): Promise<LiveMocks> {
  const m: LiveMocks = {
    posts: [], polls: [], unmocked: [], ollamaChecks: [],
    follows: { items: [], can_auto_start: true, posts: [], addStatus: 200 },
    state: { sessions: [], status: 'queued', message: 'Waiting for the GPU', cues: [], startStatus: 200, sessionsFail: false, model: null, notes: [], figures: {}, finishOnStop: false },
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
  // The picker for "Use glossary and packs from": one title with no series, one in the series "Moonlit".
  await page.route((u) => u.pathname === '/api/library/dramas', (route) =>
    json(route, { items: [
      { id: 7, title_en: 'Moonlit Letters', title_zh: '月下书信', series_id: null },
      { id: 8, title_en: 'Chapter Two', title_zh: null, series_id: 3 },
    ], count: 2 }))
  // The picker asks for one-title series too; the real endpoint lists each series with its titles.
  await page.route((u) => u.pathname === '/api/library/series', (route) =>
    json(route, { items: [{
      id: 3, name: 'Moonlit', is_private: false, owned_by_me: true, character_count: 0, glossary_term_count: 0,
      dramas: [{ id: 8, title_en: 'Chapter Two', title_zh: null, status: null, media_type: null, updated_at: null }],
    }] }))
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
  await page.route('**/api/live/follows', (route) => {
    const r = route.request()
    const f = m.follows
    if (r.method() === 'GET') {
      const body = { items: f.items.map((x) => ({ ...x })), can_auto_start: f.can_auto_start, max_per_member: 5, max_total: 20 }
      // A queued check is reported once as "checking", then its result.
      for (const x of f.items) {
        if (x.checking) Object.assign(x, { checking: false, last_checked_at: Date.now() / 1000, state: 'not_live', last_status: 'Not live' })
      }
      return json(route, body)
    }
    const body = r.postDataJSON() as { channel: string; mode?: 'notify' | 'auto'; options?: Record<string, unknown> }
    f.posts.push({ path: '/api/live/follows', body })
    if (f.addStatus !== 200) return json(route, { error: { code: 'forbidden', message: 'Not allowed.' } }, f.addStatus)
    const row = followRow(f.items.length + 1, { channel: body.channel, channel_name: '', mode: body.mode ?? 'notify' })
    f.items = [...f.items, row]
    return json(route, row)
  })
  await page.route('**/api/live/follows/**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    const f = m.follows
    f.posts.push({ path, body: r.postData() ? r.postDataJSON() : null })
    if (path === '/api/live/follows/check-all') {
      const mine = f.items.filter((x) => x.mine || f.can_auto_start)
      mine.forEach((x) => { x.checking = true })
      return json(route, { queued: mine.length, skipped: 0 })
    }
    const [, id, action] = path.match(/^\/api\/live\/follows\/(\d+)(?:\/(\w+))?$/) ?? []
    const row = f.items.find((x) => x.id === Number(id))
    if (!row) return json(route, { error: { code: 'not_found', message: 'No such followed channel.' } }, 404)
    if (action === 'remove') {
      f.items = f.items.filter((x) => x !== row)
      return json(route, { id: row.id, removed: true })
    }
    if (action === 'check') {
      row.checking = true
      return json(route, { id: row.id, queued: true })
    }
    if (action === 'start') {
      m.state.sessions = [{ session_id: SID, status: 'queued', engine: null, cue_count: 0 }]
      return json(route, { session_id: SID })
    }
    Object.assign(row, r.postDataJSON())
    return json(route, row)
  })
  await page.route('**/api/live/sessions', (route) => {
    const r = route.request()
    if (r.method() === 'GET') {
      return m.state.sessionsFail
        ? json(route, { error: { code: 'internal', message: 'Something went wrong.' } }, 500)
        : json(route, m.state.sessions)
    }
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
      session_id: SID, status, message, model: m.state.model, progress: 0, notes: m.state.notes, cues: cues.slice(after), next_index: Math.max(after, cues.length), ...m.state.figures,
    })
  })
  await page.route(`**/api/live/sessions/${SID}/stop`, (route) => {
    const r = route.request()
    m.posts.push({ url: r.url(), body: null, headers: r.headers() })
    if (m.state.finishOnStop) {
      m.state.finishOnStop = false
      return json(route, { session_id: SID, stopping: true, finishing: true })
    }
    m.state.status = 'cancelled'
    m.state.message = 'Cancelled.'
    return json(route, { session_id: SID, stopping: true, finishing: false })
  })
  return m
}

export async function openLive(page: Page) {
  await page.goto('/#/live')
  return page.getByRole('region', { name: 'Live' })
}

// A switch inside a Field must keep its own 44x24 track with the knob at the right end when on.
export async function expectSwitchKeepsItsSize(live: Locator) {
  const toggle = live.getByRole('switch', { name: 'Reply without thinking' })
  await expect(toggle).toHaveAttribute('aria-checked', 'true')
  const track = await toggle.boundingBox()
  expect(track!.width).toBeCloseTo(44, 0)
  expect(track!.height).toBeCloseTo(24, 0)
  // It turns on once the engine list loads, and the knob then slides there (a CSS
  // transition): read its place until the slide has ended.
  const knobGap = async () => {
    const thumb = await toggle.locator('.toggle-thumb').boundingBox()
    return track!.x + track!.width - (thumb!.x + thumb!.width)
  }
  await expect.poll(knobGap).toBeLessThanOrEqual(4)
  expect(await knobGap()).toBeGreaterThanOrEqual(0)
}
