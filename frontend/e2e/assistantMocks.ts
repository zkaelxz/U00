import type { Page, Route } from '@playwright/test'

import { ME } from './authMocks'

// Shared page.route mocks for the Maintenance assistant specs.
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

const TOOLS = {
  tools: [
    { name: 'search_code', description: 'Searches the source code.', tier: 'green' },
    { name: 'read_file', description: 'Reads one file in the repo.', tier: 'green' },
    { name: 'read_log', description: 'Reads the end of the app log.', tier: 'green' },
  ],
  write_tools: [],
}

interface BacklogRow {
  id: number
  kind: 'bug' | 'feature' | 'note'
  text: string
  created_at: string
}

interface Call {
  method: string
  path: string
  body: unknown
}

interface AssistantMock {
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
  /** The independent review settings and the review /ask returns. */
  rolesEnabled: boolean
  reviewEngine: string | null
  review: unknown
  /** GitHub delivery state (the token is only ever a boolean here). */
  github: { enabled: boolean; repo: string | null; base_branch: string; token_configured: boolean; branch_prefix: string }
  /** Status for POST /github/token (403 = key writes off). */
  tokenStatus: number
  /** Lead review: per cloud engine consent. When set, /ask refuses a cloud engine without it (409). */
  cloudConsent: Record<string, boolean> | null
  /** The escalation ladder. When set, /ask labels answers with their tier and refuses an unconfirmed cloud escalation. */
  tiers: string[] | null
  /** Engines whose next /ask fails as unreachable (e.g. Ollama not running). */
  failing: string[]
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
    tokenStatus: 200, tiers: null, failing: [], ...over,
  }
  const isLocal = (e: string) => e === 'ollama'
  const ladder = () =>
    (s.tiers ?? []).map((engine, i) => ({ tier: i + 1, engine, local: isLocal(engine), consent: isLocal(engine) || s.cloudConsent?.[engine] === true }))
  const tierOf = (engine: string) => {
    const i = (s.tiers ?? []).indexOf(engine)
    return { tier: i >= 0 ? i + 1 : null, next_engine: i >= 0 ? (s.tiers ?? [])[i + 1] ?? null : null }
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
    ...(s.tiers ? { tier_order: null, tiers: ladder(), engine_keys: { claude: false, gemini: true, ollama: true } } : {}),
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
    const body = route.request().postDataJSON() as { engine?: string; escalate?: boolean; consent?: boolean }
    const picked = body.engine || s.engine || 'ollama'
    if (s.tiers && body.escalate && !isLocal(picked) && body.consent !== true) {
      return json(route, { error: { code: 'conflict', message: 'Confirm first.', details: { reason: 'escalation_consent_required', engine: picked } } }, 409)
    }
    if (s.cloudConsent && picked !== 'ollama' && !s.cloudConsent[picked]) {
      return json(route, { error: { code: 'conflict', message: 'Not allowed yet.', details: { reason: 'cloud_consent_required', engine: picked } } }, 409)
    }
    if (s.failing.includes(picked)) {
      s.failing = s.failing.filter((e) => e !== picked)
      const details = { reason: 'unreachable', engine: picked, ...tierOf(picked) }
      return json(route, { error: { code: 'application_error', message: 'The engine call failed.', details } }, 500)
    }
    if (s.askStatus !== 200) {
      return json(route, { error: { code: 'dependency_unavailable', message: 'No API key for this engine.' } }, s.askStatus)
    }
    if (s.tiers) {
      const evidence = picked === 'ollama' ? 'RESULT t1 (inspect_logs, ok):\nERROR dub stage: no speaker' : ''
      // An escalation never sends the fix to a cloud reviewer the dialog didn't mention.
      const skip = body.escalate && s.rolesEnabled && s.reviewEngine && !isLocal(s.reviewEngine)
      const review = skip
        ? { review: null, review_skipped: 'The reviewer is a cloud engine, so it wasn’t asked as part of this escalation. Ask for a review separately.' }
        : { review: s.review }
      return json(route, { ...ANSWER, ...review, engine: picked, local: isLocal(picked), evidence, ...tierOf(picked) })
    }
    return json(route, { ...ANSWER, review: s.review })
  })
  await page.route(/\/api\/assistant\/report$/, (route) => {
    record(route)
    const b = route.request().postDataJSON() as { chat_history: { role: string; content: string }[] }
    const chat = b.chat_history.map((t) => `${t.role === 'user' ? 'You' : 'Assistant'}: ${t.content}`).join('\n')
    return json(route, { report: `Baihe problem report\nPrepared on this PC and not sent anywhere.\n\n== Conversation ==\n${chat}\n\n== Support report ==\nPython 3.12` })
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
  // GitHub delivery.
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

// The Assistant blocks are folds that start closed (except Ask); open one by its region name.
export async function openSection(page: Page, region: string) {
  const fold = page.getByRole('region', { name: region }).locator('details.section').first()
  if (!(await fold.evaluate((e) => (e as HTMLDetailsElement).open))) await fold.locator('summary').first().click()
}
