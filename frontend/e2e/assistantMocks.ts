import type { Page, Route } from '@playwright/test'

import { ME } from './authMocks'

// Shared page.route mocks for the Maintenance assistant specs (Step 42).
// Every /api/assistant/* call is mocked (asking would reach an AI engine);
// a guard aborts and records anything under /api/assistant that nothing
// mocks. Other GETs (library etc.) go to the seeded API.

export const ANSWER = {
  answer: 'The Dub stage skips a line when it has no speaker.\n\nSee services/dub_service.py, `plan_lines`.\n<b>not bold</b>',
  proposed_patches: [
    {
      id: 'p1',
      patch: '--- a/services/dub_service.py\n+++ b/services/dub_service.py\n@@ -10,2 +10,2 @@\n-    if not line.speaker:\n+    if not line.speaker and not default_voice:\n         continue',
      files: ['services/dub_service.py'],
    },
  ],
  suggested_backlog: [{ kind: 'bug', text: 'Dub skips lines with no speaker even when a default voice is set.' }],
  tool_calls: [
    { id: 't1', name: 'search_code', args: { query: 'speaker', path: 'services' }, ok: true, summary: '3 matches' },
    { id: 't2', name: 'read_file', args: { path: 'services/missing.py' }, ok: false, summary: 'No such file.' },
  ],
  engine: 'claude',
  model: null,
}

export const TOOLS = {
  tools: [
    { name: 'search_code', description: 'Searches the source code.', tier: 'green' },
    { name: 'read_file', description: 'Reads one file in the repo.', tier: 'green' },
    { name: 'read_log', description: 'Reads the end of the app log.', tier: 'green' },
  ],
  write_tools: [],
}

export interface BacklogRow {
  id: number
  kind: 'bug' | 'feature' | 'note'
  text: string
  created_at: string
}

export interface Call {
  method: string
  path: string
  body: unknown
}

export interface AssistantMock {
  developerMode: boolean
  local: boolean
  engine: string | null
  model: string | null
  backlog: BacklogRow[]
  /** Status for POST /ask: 200 answers ANSWER. */
  askStatus: number
  /** When set, /ask waits for this before answering. */
  askGate: Promise<void> | null
  calls: Call[]
  unmocked: string[]
  /** Step 60: the independent review settings and the review /ask returns. */
  rolesEnabled: boolean
  reviewEngine: string | null
  review: unknown
  /** Step 72: GitHub delivery state (the token is only ever a boolean here). */
  github: { enabled: boolean; repo: string | null; base_branch: string; token_configured: boolean; branch_prefix: string }
  /** Status for POST /github/token (403 = key writes off). */
  tokenStatus: number
  /** Lead review: per cloud engine consent. When set, /ask refuses a cloud engine without it (409). */
  cloudConsent: Record<string, boolean> | null
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

const FORBIDDEN = { error: { code: 'forbidden', message: 'This only works on the main PC.' } }

export async function mockAssistant(page: Page, over: Partial<AssistantMock> = {}): Promise<AssistantMock> {
  const s: AssistantMock = {
    developerMode: false, local: true, engine: null, model: null,
    backlog: [{ id: 1, kind: 'note', text: 'Tidy the Export stage copy.', created_at: '2026-09-28T09:30:00' }],
    askStatus: 200, askGate: null, calls: [], unmocked: [], cloudConsent: null,
    rolesEnabled: false, reviewEngine: null, review: null,
    github: { enabled: false, repo: null, base_branch: 'baihe-subtitler', token_configured: false, branch_prefix: 'baihe-assistant/' },
    tokenStatus: 200, ...over,
  }
  const record = (route: Route) => {
    const req = route.request()
    const url = new URL(req.url())
    let body: unknown = null
    try {
      body = req.postDataJSON()
    } catch {
      body = req.postData()
    }
    s.calls.push({ method: req.method(), path: url.pathname, body })
    return url
  }
  const settings = () => ({
    ...(s.cloudConsent ? { default_engine: 'ollama', local_engines: ['ollama'], cloud_consent: s.cloudConsent } : {}),
    developer_mode: s.developerMode, engine: s.engine, model: s.model, engine_choices: ['claude', 'gemini', 'ollama'],
    roles_enabled: s.rolesEnabled, review_engine: s.reviewEngine, review_model: null,
  })

  // Guard first: later routes take precedence.
  await page.route(/\/api\/assistant\/.*/, (route) => {
    record(route)
    s.unmocked.push(`${route.request().method()} ${route.request().url()}`)
    return route.abort()
  })
  await page.route(/\/api\/auth\/me$/, (route) => json(route, ME.authOff))
  await page.route(/\/api\/meta$/, (route) =>
    json(route, { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local: s.local }),
  )

  await page.route(/\/api\/assistant\/settings$/, (route) => {
    record(route)
    if (!s.local) return json(route, FORBIDDEN, 403)
    if (route.request().method() === 'POST') {
      const b = route.request().postDataJSON() as Partial<ReturnType<typeof settings>>
      if ('developer_mode' in b) s.developerMode = !!b.developer_mode
      if ('engine' in b) s.engine = b.engine ?? null
      if ('model' in b) s.model = b.model ?? null
      if ('roles_enabled' in b) s.rolesEnabled = !!b.roles_enabled
      if ('review_engine' in b) s.reviewEngine = b.review_engine ?? null
      const cc = (b as { cloud_consent?: Record<string, boolean> }).cloud_consent
      if (cc && s.cloudConsent) s.cloudConsent = { ...s.cloudConsent, ...cc }
    }
    return json(route, settings())
  })
  await page.route(/\/api\/assistant\/tools$/, (route) => {
    record(route)
    return json(route, TOOLS)
  })
  await page.route(/\/api\/assistant\/ask$/, async (route) => {
    record(route)
    if (s.askGate) await s.askGate
    if (!s.developerMode) return json(route, { error: { code: 'conflict', message: 'Developer Mode is off.' } }, 409)
    const picked = (route.request().postDataJSON() as { engine?: string }).engine || s.engine || 'ollama'
    if (s.cloudConsent && picked !== 'ollama' && !s.cloudConsent[picked]) {
      return json(route, { error: { code: 'conflict', message: 'Not allowed yet.', details: { reason: 'cloud_consent_required', engine: picked } } }, 409)
    }
    if (s.askStatus !== 200) {
      return json(route, { error: { code: 'dependency_unavailable', message: 'No API key for this engine.' } }, s.askStatus)
    }
    return json(route, { ...ANSWER, review: s.review })
  })
  await page.route(/\/api\/assistant\/changelog$/, (route) => {
    record(route)
    const b = route.request().postDataJSON() as { from_ref: string; to_ref?: string }
    return json(route, { changelog: '- Added the maintenance assistant.\n- Fixed dub skipping lines.', commit_count: 2, from_ref: b.from_ref, to_ref: b.to_ref ?? 'HEAD' })
  })
  await page.route(/\/api\/assistant\/backlog$/, (route) => {
    record(route)
    if (route.request().method() === 'POST') {
      const b = route.request().postDataJSON() as { kind: BacklogRow['kind']; text: string }
      const row = { id: Math.max(0, ...s.backlog.map((x) => x.id)) + 1, kind: b.kind, text: b.text, created_at: '2026-09-29T10:00:00' }
      s.backlog.push(row)
      return json(route, row)
    }
    return json(route, { items: s.backlog })
  })
  await page.route(/\/api\/assistant\/backlog\/\d+\/delete$/, (route) => {
    const url = record(route)
    const id = Number(url.pathname.split('/')[4])
    const before = s.backlog.length
    s.backlog = s.backlog.filter((x) => x.id !== id)
    return json(route, { deleted: s.backlog.length < before })
  })
  await page.route(/\/api\/assistant\/backlog\/clear$/, (route) => {
    record(route)
    const n = s.backlog.length
    s.backlog = []
    return json(route, { deleted: n })
  })
  // Step 72: GitHub delivery.
  await page.route(/\/api\/assistant\/github$/, (route) => {
    record(route)
    return json(route, s.github)
  })
  await page.route(/\/api\/assistant\/github\/settings$/, (route) => {
    record(route)
    const b = route.request().postDataJSON() as Partial<AssistantMock['github']>
    s.github = { ...s.github, ...b, base_branch: b.base_branch === null ? 'baihe-subtitler' : (b.base_branch ?? s.github.base_branch) }
    return json(route, s.github)
  })
  await page.route(/\/api\/assistant\/github\/token$/, (route) => {
    record(route)
    if (s.tokenStatus !== 200) return json(route, FORBIDDEN, s.tokenStatus)
    s.github = { ...s.github, token_configured: true }
    return json(route, { token_configured: true })
  })
  await page.route(/\/api\/assistant\/github\/token\/clear$/, (route) => {
    record(route)
    s.github = { ...s.github, token_configured: false }
    return json(route, { token_configured: false })
  })
  await page.route(/\/api\/assistant\/github\/test$/, (route) => {
    record(route)
    return json(route, { ok: true, repo: s.github.repo, default_branch: 'main', can_push: true, base_branch: s.github.base_branch, base_exists: true })
  })
  await page.route(/\/api\/assistant\/github\/preview$/, (route) => {
    record(route)
    const b = route.request().postDataJSON() as { patch: string; title: string }
    return json(route, {
      repo: s.github.repo, base_branch: s.github.base_branch, branch_prefix: 'baihe-assistant/', title: b.title,
      files: [{ path: 'services/dub_service.py', change: 'modify' }], patch: b.patch, sha256: 'f'.repeat(64),
    })
  })
  await page.route(/\/api\/assistant\/github\/deliver$/, (route) => {
    record(route)
    return json(route, {
      pr_url: 'https://github.com/me/app/pull/7', pr_number: 7, branch: 'baihe-assistant/fix-20260930-101010',
      base_branch: s.github.base_branch, repo: s.github.repo, files: [{ path: 'services/dub_service.py', change: 'modify' }],
    })
  })
  return s
}
