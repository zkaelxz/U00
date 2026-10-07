import type { Page, Route } from '@playwright/test'
import { untilTestEnds } from './stageLineMocks'

// Shared page.route mocks for the voice-clone specs. The seeded drama has no
// speakers, audio or series, so the characters, candidates, dub config, job and
// every write are mocked here. guard() goes first: any /api write nothing
// mocks is aborted and recorded (GETs fall through to the seeded API).

export const SCREENS = '/tmp/claude-0/-home-user-U00/0474fa7c-90c2-5302-85f6-dd03ce066de6/scratchpad/screens/voice-clone'

export const CAND_ID = 'ab'.repeat(16)

const character = (over: object = {}) => ({
  speaker_label: 'SPEAKER_00', character_name: 'Wei Ying', voice_actor: '', pronouns: '',
  clone_engine: 'gpt_sovits', voice_design: '', has_ref_audio: false, ref_text_present: false,
  series_character_id: null, series_character_name: '', line_count: 12, ...over,
})

const dubConfig = (over: object = {}) => ({
  drama_id: 1, content_mode: null, is_narration: false, narration_language: 'translation',
  narration_language_options: ['translation', 'original'], source_language: 'zh',
  tts_engines: [{ key: 'omnivoice', label: 'OmniVoice' }],
  default_engine: 'omnivoice',
  defaults: { max_speedup: 1.4, max_slowdown: 0.85, speedup_range: [1, 2], slowdown_range: [0.5, 1] },
  speakers: [
    {
      speaker_label: 'SPEAKER_00', character_name: 'Wei Ying',
      engine: 'omnivoice', has_clone_ref: false,
      clone_warning: 'GPT-SoVITS is chosen, but this speaker has no reference clip or voice description, so it will use the voice of the engine picked in Dub.',
    },
    {
      speaker_label: 'SPEAKER_01', character_name: 'Lan Zhan',
      engine: 'omnivoice', has_clone_ref: false, clone_warning: null,
    },
  ],
  gpu_required: false, speakable_line_count: 20, track_available: false,
  gpt_sovits_configured: false, can_keep_background: false, ...over,
})

export async function guard(page: Page): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return unmocked
}

interface Mocks {
  posts: { url: string; body: unknown; headers: Record<string, string> }[]
  // started: an extraction job exists (set by the extract POST, or by a test
  // that opens the panel while one is already running, to check reattach).
  state: { entries: ReturnType<typeof character>[]; jobDone: boolean; extracted: boolean; started: boolean }
}

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })

export async function mockVoiceClone(page: Page, opts: { remote?: boolean } = {}): Promise<Mocks> {
  const m: Mocks = {
    posts: [],
    state: {
      entries: [character(), character({ speaker_label: 'SPEAKER_01', character_name: 'Lan Zhan', clone_engine: 'omnivoice', voice_design: 'low, calm', line_count: 9 })],
      jobDone: false,
      extracted: false,
      started: false,
    },
  }
  const record = (route: Route) => {
    const r = route.request()
    let body: unknown = null
    try {
      body = r.postDataJSON()
    } catch {
      body = r.postData()
    }
    m.posts.push({ url: r.url(), body, headers: r.headers() })
  }
  const update = (label: string, patch: object) => {
    m.state.entries = m.state.entries.map((e) => (e.speaker_label === label ? { ...e, ...patch } : e))
    return m.state.entries.find((e) => e.speaker_label === label)
  }

  await page.route('**/api/meta', (route) =>
    json(route, { app: 'baihe', api_version: '1', environment: 'development', local: !opts.remote }))
  await page.route('**/api/library/dramas/1', (route) =>
    untilTestEnds(async () => {
      const resp = await route.fetch()
      await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
    }),
  )
  await page.route('**/api/dub/dramas/1/config', (route) => json(route, dubConfig()))
  await page.route('**/api/dub/dramas/1/pacing', (route) => json(route, { available: false, counts: {}, lines: [] }))
  await page.route('**/api/characters/dramas/1', (route) => json(route, m.state.entries))
  await page.route('**/api/characters/voice-bank', (route) =>
    json(route, [{ id: 3, name: 'Calm narrator', clone_engine: 'omnivoice', voice_design: '', language: 'zh', notes: '', ref_text_present: true }]))
  await page.route('**/api/characters/series/7/characters', (route) =>
    json(route, [{ id: 11, character_name: 'Wei Wuxian', aliases: '', notes: '', pronouns: 'he/him' }]))
  await page.route('**/api/characters/dramas/1/reference-clips/candidates', (route) =>
    json(route, {
      drama_id: 1,
      speakers: m.state.extracted
        ? [
            {
              speaker_label: 'SPEAKER_00',
              candidates: [
                { id: CAND_ID, start: 12, end: 18, duration: 6, ref_text: '魏婴，你在哪里' },
                { id: 'cd'.repeat(16), start: 40, end: 49.5, duration: 9.5, ref_text: '' },
              ],
              skip_reason: null, closest_duration: null,
            },
            { speaker_label: 'SPEAKER_01', candidates: [], skip_reason: 'too_short', closest_duration: 2.1 },
          ]
        : [],
    }))
  await page.route(`**/api/characters/dramas/1/reference-clips/candidates/*/audio`, (route) =>
    route.fulfill({ status: 200, contentType: 'audio/wav', body: Buffer.from('RIFF0000WAVE') }))
  await page.route('**/api/characters/dramas/1/reference-clips/extract', (route) => {
    record(route)
    m.state.started = true
    return json(route, { job_id: 'voiceref_1' })
  })
  await page.route('**/api/jobs/voiceref_1', (route) => {
    // No such job until one is started (the panel reads this id on mount).
    if (!m.state.started) return json(route, { error: { code: 'not_found', message: 'No job.' } }, 404)
    const done = m.state.jobDone
    if (done) m.state.extracted = true
    return json(route, {
      job_id: 'voiceref_1', status: done ? 'done' : 'running', progress: done ? 1 : 0.5,
      message: done ? '2 candidate clip(s) ready.' : 'Cutting clip 1 of 2...', error: null,
      description: null, gpu_touching: false, started_at: 1, finished_at: done ? 2 : null, updated_at: 2,
      result: done ? { candidate_count: 2 } : null, outcome: done ? 'ok' : null,
    })
  })
  await page.route(`**/api/characters/dramas/1/reference-clips/candidates/*/choose`, (route) => {
    record(route)
    return json(route, update('SPEAKER_00', { has_ref_audio: true, ref_text_present: true }))
  })
  await page.route('**/api/characters/dramas/1/reference-clip', (route) => {
    record(route)
    if (opts.remote) return json(route, { error: { code: 'forbidden', message: 'Not allowed.' } }, 403)
    return json(route, update('SPEAKER_00', { has_ref_audio: true }))
  })
  await page.route('**/api/characters/dramas/1/reference-clip/remove', (route) => {
    record(route)
    return json(route, update('SPEAKER_00', { has_ref_audio: false, ref_text_present: false }))
  })
  await page.route('**/api/characters/dramas/1/character', (route) => {
    record(route)
    const body = route.request().postDataJSON() as { speaker_label: string; voice_actor?: string }
    return json(route, update(body.speaker_label, { voice_actor: body.voice_actor ?? '' }))
  })
  await page.route('**/api/characters/dramas/1/series-link', (route) => {
    record(route)
    const body = route.request().postDataJSON() as { speaker_label: string; series_character_id: number | null }
    return json(route, update(body.speaker_label, body.series_character_id
      ? { series_character_id: body.series_character_id, character_name: 'Wei Wuxian' }
      : { series_character_id: null }))
  })
  await page.route('**/api/characters/dramas/1/voice-bank/save', (route) => {
    record(route)
    return json(route, { id: 9, name: 'Wei voice', clone_engine: 'omnivoice', voice_design: '', language: 'zh', notes: '', ref_text_present: true })
  })
  await page.route('**/api/characters/dramas/1/voice-bank/apply', (route) => {
    record(route)
    return json(route, update('SPEAKER_01', { has_ref_audio: true }))
  })
  return m
}

export async function openVoices(page: Page) {
  await page.goto('/#/drama/1/dub')
  const summary = page.locator('summary', { hasText: 'Voices and cloning' }).first()
  // The section starts open; click only a closed one.
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
  return page.getByRole('region', { name: 'Voices and cloning' })
}
