// Static guard: every HTTP call goes through api/client.ts, which adds
// X-CSRF-Token (signed in), X-Baihe-Local and the 401 -> signed-out switch.
// A raw fetch/XHR/beacon/EventSource or a native <form action> elsewhere
// would skip those, and with sign-in on its mutation would 403.
import { describe, expect, it } from 'vitest'

const sources = import.meta.glob<string>(['/src/**/*.{ts,tsx}', '!/src/**/*.test.ts'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

// The one file allowed to call fetch.
const CLIENT = '/src/api/client.ts'

// Comments are stripped first so prose like "fetch() the overview" can't trip it.
function code(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1')
}

// Request options (a method) may only be built in the api/ layer, which
// hands them to client.ts; pages, components and hooks call typed functions.
const API_LAYER = (file: string) => file.startsWith('/src/api/')

const RULES: { name: string; re: RegExp; allowed?: (file: string) => boolean; where?: string }[] = [
  { name: 'direct fetch() call', re: /(^|[^\w.])(window\.|globalThis\.|self\.)?fetch\s*\(/, allowed: (f) => f === CLIENT, where: 'api/client.ts' },
  // An injected fetch called straight on an API URL skips client.ts too
  // (how export.ts's ASS POST went out without X-CSRF-Token).
  { name: 'fetch called on apiUrl()', re: /(\w|\))\s*\(\s*apiUrl\s*\(|\bfetchImpl\s*\(/, allowed: (f) => f === CLIENT, where: 'api/client.ts' },
  { name: 'request method literal', re: /\bmethod\s*:\s*['"`]/, allowed: API_LAYER, where: 'api/' },
  { name: 'XMLHttpRequest', re: /\bXMLHttpRequest\b/ },
  { name: 'navigator.sendBeacon', re: /\bsendBeacon\b/ },
  // The one push stream (a GET: no CSRF token or local header to add; a 401
  // shows up on the pages' own GETs, which go through client.ts).
  { name: 'EventSource', re: /\bEventSource\b/, allowed: (f) => f === '/src/api/eventStream.ts', where: 'api/eventStream.ts' },
  { name: 'WebSocket', re: /\bWebSocket\b/ },
  { name: 'native form submission (<form action/method>)', re: /<form\b[^>]*\b(action|method)\s*=/ },
  { name: 'fetch credentials override', re: /\bcredentials\s*:/ },
  // Sign-in tokens live only in cookies (session HttpOnly, CSRF read per request).
  { name: 'token kept in web storage', re: /(local|session)Storage[\s\S]{0,80}?(token|csrf|session_id|id_token|access_token)/i },
]

describe('no request bypasses api/client.ts', () => {
  it('found the source files', () => {
    expect(Object.keys(sources)).toContain(CLIENT)
    expect(Object.keys(sources)).toContain('/src/api/export.ts')
    expect(Object.keys(sources).length).toBeGreaterThan(50)
  })

  for (const rule of RULES) {
    it(`no ${rule.name}${rule.where ? ` outside ${rule.where}` : ''}`, () => {
      const hits = Object.entries(sources)
        .filter(([file]) => !rule.allowed?.(file))
        .flatMap(([file, src]) =>
          code(src)
            .split('\n')
            .map((line, i) => (rule.re.test(line) ? `${file.slice(1)}:${i + 1}: ${line.trim()}` : null))
            .filter((x): x is string => x !== null),
        )
      expect(hits).toEqual([])
    })
  }

  it('the rules catch what they should', () => {
    const t = (name: string, s: string) => RULES.find((r) => r.name === name)!.re.test(s)
    expect(t('direct fetch() call', "await fetch('/api/x', { method: 'POST' })")).toBe(true)
    expect(t('direct fetch() call', 'window.fetch(url)')).toBe(true)
    expect(t('direct fetch() call', 'refetch()')).toBe(false)
    expect(t('direct fetch() call', 'resp.fetchBody(x)')).toBe(false)
    expect(t('fetch called on apiUrl()', 'resp = await f(apiUrl(path), init)')).toBe(true)
    expect(t('fetch called on apiUrl()', 'await fetchImpl(url, init)')).toBe(true)
    expect(t('fetch called on apiUrl()', 'export const u = (id: number) => apiUrl(`/x/${id}`)')).toBe(false)
    expect(t('request method literal', "{ method: 'DELETE' }")).toBe(true)
    expect(t('native form submission (<form action/method>)', '<form action="/api/x" method="post">')).toBe(true)
    expect(t('native form submission (<form action/method>)', '<form onSubmit={submit}>')).toBe(false)
    expect(t('token kept in web storage', "localStorage.setItem('csrf', v)")).toBe(true)
    expect(t('token kept in web storage', "sessionStorage?.setItem(SESSION_KEY, 'remote')")).toBe(false)
  })
})
