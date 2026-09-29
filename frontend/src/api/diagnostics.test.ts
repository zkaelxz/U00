import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  getDiagnostics, getJobHistory, getLog, getModelCache, getPyannote, getSetupChecks, getSupportReport,
  installDependency, resetLibrary, upgradeDependency,
} from './diagnostics'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('diagnostics api', () => {
  it('GETs /api/diagnostics', async () => {
    const { mock, f } = reply(200, { dependencies: {} })
    const out = await getDiagnostics(f)
    expect(mock.mock.calls[0][0]).toBe('/api/diagnostics')
    expect(out.dependencies).toEqual({})
  })

  it('reads the admin sections from their own paths', async () => {
    const { mock, f } = reply(200, {})
    await getSetupChecks(f)
    await getModelCache(f)
    await getJobHistory(f)
    await getSupportReport(f)
    await getPyannote(false, f)
    await getPyannote(true, f)
    expect(mock.mock.calls.map((c) => c[0])).toEqual([
      '/api/diagnostics/setup-checks',
      '/api/diagnostics/model-cache',
      '/api/diagnostics/job-history',
      '/api/diagnostics/support-report',
      '/api/diagnostics/pyannote',
      '/api/diagnostics/pyannote?check_access=true',
    ])
  })

  it('log sends n and a trimmed keyword only when set, within the server caps', async () => {
    const { mock, f } = reply(200, { lines: [] })
    await getLog(50, '', f)
    await getLog(100, '  ERROR ', f)
    await getLog(999, 'x'.repeat(150), f)
    const [u1, u2, u3] = mock.mock.calls.map((c) => c[0] as string)
    expect(u1).toBe('/api/diagnostics/log?n=50')
    expect(u2).toBe('/api/diagnostics/log?n=100&keyword=ERROR')
    expect(u3).toBe(`/api/diagnostics/log?n=200&keyword=${'x'.repeat(100)}`)
  })

  it('install and upgrade post confirm with the PC-only header, name encoded', async () => {
    const { mock, f } = reply(200, { package: 'pyannote.audio', ok: true, output_tail: [] })
    await installDependency('pyannote.audio', f)
    await upgradeDependency('yt-dlp', '2026.9.1', f)
    const [[u1, i1], [u2, i2]] = mock.mock.calls
    expect(u1).toBe('/api/diagnostics/dependencies/pyannote.audio/install')
    expect(u2).toBe('/api/diagnostics/dependencies/yt-dlp/upgrade')
    expect(JSON.parse(i1.body)).toEqual({ confirm: true })
    expect(JSON.parse(i2.body)).toEqual({ confirm: true, target: '2026.9.1' })
    expect(i1.method).toBe('POST')
    expect(localHeader(i1)).toBe('1')
  })

  it('reset sends RESET and a 403 marks the tab remote', async () => {
    const { mock, f } = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(resetLibrary(f)).rejects.toMatchObject({ status: 403 })
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/diagnostics/reset-library')
    expect(JSON.parse(init.body)).toEqual({ confirm: true, confirm_text: 'RESET' })
    expect(localHeader(init)).toBe('1')
    expect(getPcMode()).toBe('remote')
  })
})
