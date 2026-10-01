import type { Page, Route } from '@playwright/test'

// Model re-evaluation (Step 40b) mocks shared by lab-reeval.spec.ts and
// lab-reeval.mobile.spec.ts. The e2e API has no keys and its production
// model is Claude, so anything that would run or promote goes through this
// stateful stand-in for /api/models/reeval*; the real API is used only for
// reads and no-spend writes. Engines here (Ollama, NLLB) need no
// key in the real /api/benchmark/options, so "Run now" can be enabled.

type Json = Record<string, unknown>

interface Call {
  method: string
  path: string
  body: Json | null
}

const decision = (id: number, candidateId: number, kind: string, reason: string, at: string, scores: Json = {}): Json => ({
  id, candidate_id: candidateId, decision: kind, reason, scores, decided_at: at,
  summary: `Already evaluated on ${at.slice(0, 10)}, ${kind}: ${reason || 'no reason given'}`,
})

const cand = (o: Json): Json => ({
  capability: 'translation', model: null, note: '', status: 'candidate', created_at: '2026-09-20T10:00:00', last_decision: null, ...o,
})

const PRODUCTION = { engine: 'ollama', model: 'qwen3:8b', source: 'promoted', promoted_at: '2026-08-14T09:30:00' }

const REJECTED_DECISION = decision(1, 3, 'rejected', 'Mangled honorifics in the regression set', '2026-09-02T08:15:00', {
  aggregate_score: 0.74, production_score: 0.81,
})

function candidates(): Json[] {
  return [
    cand({ id: 1, engine: 'ollama', model: 'qwen2.5:14b', note: 'Bigger; check VRAM' }),
    cand({ id: 2, engine: 'nllb', note: 'Free server fallback', created_at: '2026-09-21T11:00:00' }),
    cand({ id: 3, engine: 'gemini', model: 'gemini-flash-latest', status: 'rejected', created_at: '2026-08-30T09:00:00', last_decision: REJECTED_DECISION }),
  ]
}

const PRODUCTION_RUN = {
  id: 100, status: 'done', aggregate_score: 0.812, total_cost_usd: 0, avg_latency_seconds: 1.42, peak_vram_mb: 5400,
  engine: 'ollama', model: 'qwen3:8b',
}

function reportRows(cands: Json[]): Json[] {
  const byId = (id: number) => cands.find((c) => c.id === id) as Json
  return [
    {
      candidate: byId(1), run_id: 101, status: 'done', aggregate_score: 0.846, quality_delta: 0.034, cost_delta_usd: 0,
      latency_delta_seconds: 0.61, vram_delta_mb: 3900, total_cost_usd: 0, avg_latency_seconds: 2.03,
    },
    {
      candidate: byId(2), run_id: 102, status: 'done', aggregate_score: 0.702, quality_delta: -0.11, cost_delta_usd: 0,
      latency_delta_seconds: -0.9, vram_delta_mb: -5400, total_cost_usd: 0, avg_latency_seconds: 0.52,
    },
  ]
}

export function overview(o: { withReport?: boolean; error?: string | null } = {}): Json {
  const cands = candidates()
  return {
    capability: 'translation',
    production: { ...PRODUCTION },
    settings: { schedule_enabled: false, interval_days: 30, tier: null, set_name: null, max_cost_usd: null },
    next_due_at: null,
    candidates: cands,
    report: {
      production: { ...PRODUCTION },
      production_run: o.withReport ? { ...PRODUCTION_RUN } : null,
      started_at: o.withReport ? '2026-09-29T03:00:12' : null,
      scheduled: !!o.withReport,
      arena_group: o.withReport ? 'g-1' : null,
      error: o.error ?? null,
      error_at: o.error ? '2026-09-30T03:00:05' : null,
      rows: o.withReport ? reportRows(cands) : [],
    },
  }
}

const ESTIMATE = {
  stage: 'translation', case_count: 12,
  configs: [
    { engine: 'ollama', model: 'qwen3:8b', estimated_cost_usd: 0, cap_applies: false },
    { engine: 'ollama', model: 'qwen2.5:14b', estimated_cost_usd: 0, cap_applies: false },
    { engine: 'nllb', model: null, estimated_cost_usd: 0, cap_applies: false },
  ],
  estimated_cost_usd: 0, monthly_cap_usd: 5, month_spend_usd: 1.2, remaining_usd: 3.8, monthly_refusal: null, estimate_above_cap: false,
}

const job = (status: string, progress: number | null) => ({
  job_id: 'benchmark_lab', status, progress, message: status === 'done' ? 'Finished' : 'Case 6 of 12', error: null,
  description: 'Benchmark run', gpu_touching: false, started_at: 1, finished_at: status === 'done' ? 2 : null, updated_at: 2,
  result: null, outcome: status === 'done' ? 'ok' : null, outcome_message: null,
})

const now = () => '2026-09-30T12:00:00'

/**
 * Installs the stand-in. Returns the recorded calls and the live state (tests
 * may edit it before the page loads). Any other non-GET /api call is
 * aborted and recorded in `unmocked` (registered first, so it is tried last).
 */
export async function mockReeval(page: Page, start: Json = overview()) {
  const state = { overview: structuredClone(start) as Json, decisions: [] as Json[], jobPolls: 0, runStarted: false }
  const calls: Call[] = []
  const unmocked: string[] = []
  const ov = () => state.overview as { candidates: Json[]; settings: Json; production: Json; report: Json }

  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })

  const handle = async (route: Route) => {
    const r = route.request()
    const url = new URL(r.url())
    const path = url.pathname.replace(/^\/api\/models\/reeval/, '') || '/'
    const body = r.postData() ? (JSON.parse(r.postData() as string) as Json) : null
    const method = r.method()
    calls.push({ method, path, body })
    const o = ov()
    const find = (id: number) => o.candidates.find((c) => c.id === id) as Json
    const idMatch = /^\/candidates\/(\d+)\/(reject|reopen|promote)$/.exec(path)

    if (method === 'GET' && path === '/') return route.fulfill({ json: state.overview })
    if (method === 'GET' && path === '/decisions') return route.fulfill({ json: { decisions: state.decisions } })
    if (method === 'POST' && path === '/estimate') return route.fulfill({ json: ESTIMATE })
    if (method === 'POST' && path === '/settings') {
      o.settings = { ...body }
      o.next_due_at = body?.schedule_enabled ? '2026-10-14T12:00:00' : null
      return route.fulfill({ json: state.overview })
    }
    if (method === 'POST' && path === '/candidates') {
      const existing = o.candidates.find((c) => c.engine === body?.engine && (c.model ?? null) === (body?.model ?? null))
      if (existing) return route.fulfill({ json: { candidate: existing, already_registered: true } })
      const c = cand({ id: 10 + o.candidates.length, engine: body?.engine, model: body?.model ?? null, note: body?.note ?? '', created_at: now() })
      o.candidates.push(c)
      return route.fulfill({ json: { candidate: c, already_registered: false } })
    }
    if (method === 'POST' && idMatch) {
      const c = find(Number(idMatch[1]))
      if (idMatch[2] === 'reopen') {
        c.status = 'candidate'
      } else if (idMatch[2] === 'reject') {
        c.status = 'rejected'
        c.last_decision = decision(50 + state.decisions.length, c.id as number, 'rejected', String(body?.reason ?? ''), now())
        state.decisions.unshift(c.last_decision as Json)
      } else {
        if (body?.confirm !== true) return route.fulfill({ status: 422, json: { error: { code: 'invalid_input', message: 'Confirmation required (confirm=true).' } } })
        const previous = { ...o.production }
        o.production = { engine: c.engine, model: c.model, source: 'promoted', promoted_at: now() }
        c.status = 'promoted'
        c.last_decision = decision(50 + state.decisions.length, c.id as number, 'promoted', String(body?.reason ?? ''), now(), { aggregate_score: 0.846, production_score: 0.812 })
        state.decisions.unshift(c.last_decision as Json)
        return route.fulfill({
          json: { production: o.production, previous, default_engine_changed: previous.engine !== c.engine, candidate: c },
        })
      }
      return route.fulfill({ json: { candidate: c } })
    }
    if (method === 'POST' && path === '/run') {
      if (body?.confirm !== true) return route.fulfill({ status: 422, json: { error: { code: 'invalid_input', message: 'Confirmation required (confirm=true).' } } })
      state.runStarted = true
      state.jobPolls = 0
      o.report = { ...(overview({ withReport: true }).report as Json), scheduled: false, started_at: now() }
      ;(o.report as { rows: Json[] }).rows.forEach((row) => (row.status = 'running'))
      ;(o.report as { production_run: Json }).production_run.status = 'running'
      return route.fulfill({
        json: { job_id: 'benchmark_lab', session_ids: [100, 101, 102], arena_group: 'g-2', estimated_cost_usd: 0, candidate_ids: [1, 2] },
      })
    }
    return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'Not mocked.' } } })
  }
  await page.route(/\/api\/models\/reeval(\/|\?|$)/, handle)

  // The run's job: running twice, then done (and the report's runs finish with it).
  await page.route('**/api/jobs/benchmark_lab', (route) => {
    if (!state.runStarted) return route.continue()
    state.jobPolls += 1
    if (state.jobPolls <= 2) return route.fulfill({ json: job('running', 0.5) })
    const rep = ov().report as { rows: Json[]; production_run: Json }
    rep.rows.forEach((row) => (row.status = 'done'))
    rep.production_run.status = 'done'
    return route.fulfill({ json: job('done', 1) })
  })

  // The Arena for [production run, candidate run]: two runs, no cases.
  await page.route('**/api/benchmark/arena?*', (route) => {
    const ids = new URL(route.request().url()).searchParams.getAll('run_ids').map(Number)
    const run = (id: number, engine: string, model: string | null, score: number) => ({
      id, label: 'Re-evaluation', stage: 'translation', engine, model, prompt_version: '', arena_group: 'g-1', status: 'done',
      case_count: 12, scored_count: 12, passed_count: 9, error_count: 0, aggregate_score: score, avg_latency_seconds: 1.2,
      total_cost_usd: 0, peak_vram_mb: null, note: null, created_at: '2026-09-29T03:00:12', finished_at: null,
      context_settings: {}, case_filter: {}, delta_vs_first: id === ids[0] ? null : score - 0.812,
    })
    return route.fulfill({ json: { runs: [run(ids[0], 'ollama', 'qwen3:8b', 0.812), run(ids[1], 'ollama', 'qwen2.5:14b', 0.846)], rows: [] } })
  })

  return { state, calls, unmocked }
}

/** Viewer not on the main PC: /api/meta says local: false. */
export async function mockRemote(page: Page) {
  await page.route('**/api/meta', (route) =>
    route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }),
  )
}
