import { describe, expect, it, vi } from 'vitest'

import { getDiagnostics } from './diagnostics'

describe('getDiagnostics', () => {
  it('GETs /api/diagnostics', async () => {
    const f = vi.fn(async (..._a: unknown[]) => new Response(JSON.stringify({ dependencies: {} }), { status: 200 }))
    const out = await getDiagnostics(f as unknown as typeof fetch)
    expect(f.mock.calls[0][0]).toBe('/api/diagnostics')
    expect(out.dependencies).toEqual({})
  })
})
