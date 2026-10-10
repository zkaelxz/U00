import { type Page } from '@playwright/test'

// A stateful /api/queue for the Queue card on the Jobs page: add, move,
// remove, retry, clear finished, start, pause, schedule and policy change the
// state the next GET returns. Titles come from jobsMenuMocks' DRAMA_TITLES.

type Item = Record<string, unknown> & { id: number; status: string; mine: boolean }

export interface QueueMock {
  state: Record<string, unknown> & { items: Item[] }
  calls: string[]
}

const TITLES: Record<number, string> = { 3: 'Signal', 4: 'Kae' }

export function queueItem(id: number, p: Partial<Item> = {}): Item {
  return {
    id, drama_id: 3, title: 'Signal', step: 'transcribe', status: 'waiting', reason: null, job_id: null,
    engine: null, paid: false, mine: true, added_at: 1, started_at: null, finished_at: null, ...p,
  }
}

export async function mockQueueApi(page: Page, items: Item[] = [], extra: Record<string, unknown> = {}): Promise<QueueMock> {
  const mock: QueueMock = {
    state: {
      version: 1, state: 'idle', stop_on_failure: false, schedule: null, notice: null, max_items: 200,
      can_control: true, monthly_cap_usd: 5, items, ...extra,
    },
    calls: [],
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
    if (r.method() === 'POST') mock.calls.push(path.replace('/api/queue', '') || '/')
    const find = () => s.items.findIndex((i) => i.id === Number(path.split('/')[4]))
    if (r.method() === 'POST' && path === '/api/queue/items') {
      const id = Number(body.drama_id)
      s.items = [...s.items, queueItem(nextId++, {
        drama_id: id, title: TITLES[id], step: body.step,
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
