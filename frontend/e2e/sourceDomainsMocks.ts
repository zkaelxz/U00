import type { Page, Route } from '@playwright/test'

// Mocks for the per-source address lists (/api/source-domains). Register
// after mockSources. `available: false` answers 404 like an older server.

export interface DomainEntry {
  source: string; display_name: string; domains: string[]; default_domains: string[]
  customized: boolean; last_good: string | null; pending_proposals: number
}

const DOMAIN_ENTRIES: DomainEntry[] = [
  {
    source: 'alpha', display_name: 'Alpha Comics', domains: ['alpha.example', 'alpha-mirror.example'],
    default_domains: ['alpha.example', 'alpha-mirror.example'], customized: false, last_good: 'alpha.example', pending_proposals: 1,
  },
  {
    source: 'beta', display_name: 'Beta Novels', domains: ['beta.example'],
    default_domains: ['beta.example'], customized: false, last_good: null, pending_proposals: 0,
  },
]

export interface DomainsState {
  entries: DomainEntry[]
  proposals: { source: string; display_name: string; host: string; found_at: number }[]
  calls: { path: string; body: unknown; headers: Record<string, string> }[]
  available: boolean
  // When set, saves answer 422 with this message.
  saveError: string | null
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

export async function mockDomains(page: Page, over: Partial<DomainsState> = {}): Promise<DomainsState> {
  const s: DomainsState = {
    entries: DOMAIN_ENTRIES.map((e) => ({ ...e })),
    proposals: [{ source: 'alpha', display_name: 'Alpha Comics', host: 'alpha-new.example', found_at: 1_700_000_000 }],
    calls: [], available: true, saveError: null, ...over,
  }
  await page.route(/\/api\/source-domains(\/.*)?(\?.*)?$/, (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    if (!s.available) return json(route, { error: { code: 'not_found', message: 'Not found.' } }, 404)
    if (req.method() === 'GET') return json(route, path.endsWith('/proposals') ? s.proposals : s.entries)
    const body = (req.postDataJSON() ?? {}) as Record<string, unknown>
    s.calls.push({ path, body, headers: req.headers() })
    const find = (name: string) => s.entries.find((e) => e.source === name)!
    if (path.endsWith('/proposals/confirm')) {
      const e = find(body.source as string)
      e.domains = [body.host as string, ...e.domains.filter((d) => d !== body.host)]
      e.last_good = body.host as string
      e.customized = true
      s.proposals = s.proposals.filter((p) => p.host !== body.host)
      e.pending_proposals = s.proposals.filter((p) => p.source === e.source).length
      return json(route, e)
    }
    if (path.endsWith('/proposals/dismiss')) {
      s.proposals = s.proposals.filter((p) => p.host !== body.host)
      find(body.source as string).pending_proposals = 0
      return json(route, { dismissed: true })
    }
    const e = find(path.split('/')[3])
    if (path.endsWith('/reset')) {
      e.domains = [...e.default_domains]
      e.customized = false
      return json(route, e)
    }
    if (s.saveError) return json(route, { error: { code: 'validation_error', message: s.saveError } }, 422)
    e.domains = body.domains as string[]
    e.customized = e.domains.join() !== e.default_domains.join()
    return json(route, e)
  })
  return s
}
