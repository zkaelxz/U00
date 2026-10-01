import { describe, expect, it, vi } from 'vitest'
import { clearGithubToken, deliverGithubPr, previewGithubPr, setGithubToken } from './assistantGithub'

function fakeFetch(body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
}

describe('assistant GitHub API', () => {
  it('sends the token once, with confirm, and never expects it back', async () => {
    const f = fakeFetch({ token_configured: true })
    expect(await setGithubToken('ghp_x', f as unknown as typeof fetch)).toEqual({ token_configured: true })
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/api/assistant/github/token')
    expect(JSON.parse(String(init.body))).toEqual({ value: 'ghp_x', confirm: true })
  })
  it('clear, preview and deliver post the expected bodies', async () => {
    const f = fakeFetch({})
    await clearGithubToken(f as unknown as typeof fetch)
    await previewGithubPr('--- a/x\n', 'Fix', f as unknown as typeof fetch)
    await deliverGithubPr('--- a/x\n', 'Fix', 'why', 'a'.repeat(64), f as unknown as typeof fetch)
    const bodies = f.mock.calls.map((c) => JSON.parse(String((c as unknown as [string, RequestInit])[1].body)))
    expect(bodies).toEqual([
      { confirm: true },
      { patch: '--- a/x\n', title: 'Fix' },
      { patch: '--- a/x\n', title: 'Fix', body: 'why', sha256: 'a'.repeat(64), confirm: true },
    ])
  })
})
