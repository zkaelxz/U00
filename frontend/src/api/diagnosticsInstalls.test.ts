import { afterEach, describe, expect, it, vi } from 'vitest'

import { getDenoStatus, getUpgradeCheck, installDeno, testUpgrade } from './diagnosticsInstalls'
import { getPcMode, resetPcModeForTests } from './pcOnly'
import { voiceBankAudioUrl } from './voiceBankAudio'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('diagnostics installs api', () => {
  it('reads the Deno and Test first status', async () => {
    const { mock, f } = reply(200, {})
    await getDenoStatus(f)
    await getUpgradeCheck(f)
    expect(mock.mock.calls.map((c) => c[0])).toEqual(['/api/diagnostics/deno', '/api/diagnostics/upgrade-check'])
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

  it('Test first posts the confirmed target, name encoded; a 403 marks the tab remote', async () => {
    const { mock, f } = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(testUpgrade('pyannote.audio', '4.0.1', f)).rejects.toMatchObject({ status: 403 })
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/diagnostics/dependencies/pyannote.audio/test-upgrade')
    expect(JSON.parse(init.body)).toEqual({ confirm: true, target: '4.0.1' })
    expect(localHeader(init)).toBe('1')
    expect(getPcMode()).toBe('remote')
  })

  it('voice-bank audio URL', () => {
    expect(voiceBankAudioUrl(7)).toBe('/api/library/voice-bank/7/audio')
  })
})
