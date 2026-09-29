import { afterEach, describe, expect, it, vi } from 'vitest'

import { getDenoStatus, installDeno } from './diagnosticsInstalls'
import { resetPcModeForTests } from './pcOnly'
import { voiceBankAudioUrl } from './voiceBankAudio'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('diagnostics installs api', () => {
  it('reads the Deno status', async () => {
    const { mock, f } = reply(200, {})
    await getDenoStatus(f)
    expect(mock.mock.calls.map((c) => c[0])).toEqual(['/api/diagnostics/deno'])
  })

  it('Deno install sends only the confirm, PC only', async () => {
    const { mock, f } = reply(200, { job_id: 'deno_install', started: true })
    const out = await installDeno(f)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/diagnostics/deno/install')
    expect(JSON.parse(init.body)).toEqual({ confirm: true })
    expect(init.method).toBe('POST')
    expect(localHeader(init)).toBe('1')
    expect(out.job_id).toBe('deno_install')
  })

  it('voice-bank audio URL', () => {
    expect(voiceBankAudioUrl(7)).toBe('/api/library/voice-bank/7/audio')
  })
})
