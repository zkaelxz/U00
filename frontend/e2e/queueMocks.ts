import { type Page } from '@playwright/test'

// A stateful /api/queue for the Queue card on the Jobs page: add, move,
// remove, retry, clear finished, start, pause, schedule and policy change the
// state the next GET returns. Titles come from jobsMenuMocks' DRAMA_TITLES.

type Item = Record<string, unknown> & { id: number; status: string; mine: boolean }

export interface QueueMock {
  state: Record<string, unknown> & { items: Item[] }
  calls: string[]
  // "drama_id:step" of every Saved settings read.
  settingsReads: string[]
  // Editable fields the settings read returns per step; none, like an older server.
  fields: Record<string, unknown[]>
  // Body of every add-item call.
  addBodies: Record<string, unknown>[]
}

const SETTINGS_ROWS: Record<string, { label: string; value: string; engine?: string }[]> = {
  transcribe: [
    { label: 'Speech model', value: 'Whisper small' },
    { label: 'Source language', value: 'Chinese' },
    { label: 'Beam size', value: '5' },
    { label: 'Groq (paid)', value: 'Off' },
  ],
  translate: [
    { label: 'Engine and model', value: 'claude-sonnet-5-5', engine: 'claude' },
    { label: 'Tone and character voice', value: 'Audio drama (spoken aloud)' },
    { label: 'English variant', value: 'en-US' },
    { label: 'Batch size', value: '20' },
    { label: 'Thinking', value: 'Off' },
    { label: 'Glossary', value: 'Off, no glossary terms yet' },
    { label: 'Language packs', value: 'Off' },
    { label: 'VTuber names', value: 'Off' },
    { label: 'Reply check', value: 'Off' },
    { label: 'Off-peak start', value: 'Off: the queue runs it right away' },
    { label: 'Cost', value: 'About $0.40; uses your API key and costs money' },
    { label: 'Monthly cap left', value: '$5.00 of $5.00' },
  ],
  export: [{ label: 'Format', value: 'SRT' }, { label: 'Language', value: 'English' }],
}

const TITLES: Record<number, string> = { 3: 'Signal', 4: 'Kae' }

export function queueItem(id: number, p: Partial<Item> = {}): Item {
  return {
    id, drama_id: 3, title: 'Signal', step: 'transcribe', status: 'waiting', reason: null, job_id: null,
    engine: null, paid: false, changed_settings: [], mine: true, added_at: 1, started_at: null, finished_at: null, ...p,
  }
}

export async function mockQueueApi(page: Page, items: Item[] = [], extra: Record<string, unknown> = {}): Promise<QueueMock> {
  const mock: QueueMock = {
    state: {
      version: 1, state: 'idle', stop_on_failure: false, schedule: null, notice: null, max_items: 200,
      can_control: true, monthly_cap_usd: 5, items, ...extra,
    },
    calls: [],
    settingsReads: [],
    fields: {},
    addBodies: [],
  }
  let nextId = 100
  await page.route('**/api/translate-run/dramas/*/config', (route) => route.fulfill({
    json: {
      drama_id: 3, translation_engine: 'claude', monthly_cap_usd: 5, month_spend: 0, cap_applies_by_engine: { claude: true },
      engines: [{ name: 'claude', label: 'Claude', free: false, models: null, key_configured: true }],
    },
  }))
  await page.route('**/api/queue**', async (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    const body = r.postDataJSON?.() ?? {}
    const s = mock.state
    if (r.method() === 'GET' && path === '/api/queue/settings') {
      const q = new URL(r.url()).searchParams
      const step = q.get('step') ?? ''
      mock.settingsReads.push(`${q.get('drama_id')}:${step}`)
      return route.fulfill({ json: { drama_id: Number(q.get('drama_id')), step, title: 'Signal', rows: SETTINGS_ROWS[step] ?? [], fields: mock.fields[step] ?? [] } })
    }
    if (r.method() === 'POST') mock.calls.push(path.replace('/api/queue', '') || '/')
    const find = () => s.items.findIndex((i) => i.id === Number(path.split('/')[4]))
    if (r.method() === 'POST' && path === '/api/queue/items') {
      const id = Number(body.drama_id)
      mock.addBodies.push(body)
      const changed = Object.entries(body.settings ?? {}).map(([label, value]) => ({ label, value: String(value) }))
      s.items = [...s.items, queueItem(nextId++, {
        drama_id: id, title: TITLES[id], step: body.step, changed_settings: changed,
        ...(body.step === 'translate' ? { engine: 'claude', paid: true } : {}),
      })]
    } else if (path.endsWith('/move')) {
      const n = find()
      const m = body.direction === 'up' ? n - 1 : n + 1
      if (m >= 0 && m < s.items.length) {
        const copy = [...s.items];
        [copy[n], copy[m]] = [copy[m], copy[n]]
        s.items = copy
      }
    } else if (path.endsWith('/remove')) {
      s.items = s.items.filter((_, n) => n !== find())
    } else if (path.endsWith('/retry')) {
      s.items = s.items.map((i, n) => (n === find() ? { ...i, status: 'waiting', reason: null } : i))
    } else if (path.endsWith('/clear-finished')) {
      s.items = s.items.filter((i) => !['done', 'failed', 'skipped', 'interrupted', 'cancelled'].includes(i.status))
    } else if (path.endsWith('/start')) {
      s.state = 'running'
      s.schedule = null
      s.items = s.items.map((i, n) => (n === s.items.findIndex((x) => x.status === 'waiting') ? { ...i, status: 'running' } : i))
    } else if (path.endsWith('/pause')) {
      s.state = 'paused'
    } else if (path.endsWith('/schedule')) {
      s.schedule = body.time ? { time: body.time, start_at: Date.now() / 1000 + 3600, missed: false } : null
    } else if (path.endsWith('/policy')) {
      s.stop_on_failure = !!body.stop_on_failure
    }
    await route.fulfill({ json: s })
  })
  return mock
}

// What the server returns for a Transcribe step: each setting starting at the title's saved value.
export const TRANSCRIBE_FIELDS = [
  {
    key: 'whisper_size', label: 'Whisper model', kind: 'select', value: 'small', min: null, max: null, locked: null,
    options: [{ value: 'small', label: 'small' }, { value: 'large-v3-turbo', label: 'large-v3-turbo' }],
  },
  { key: 'beam_size', label: 'Beam size', kind: 'number', value: 5, options: [], min: 1, max: 10, locked: null },
  { key: 'separate_vocals_first', label: 'Separate vocals first', kind: 'toggle', value: false, options: [], min: null, max: null, locked: null },
  {
    key: 'use_groq', label: 'Groq (paid)', kind: 'toggle', value: false, options: [], min: null, max: null,
    locked: 'Needs paid-engine permission.',
  },
]
