import { afterEach, describe, expect, it, vi } from 'vitest'

import { resetPcModeForTests } from './pcOnly'
import { clearVoice, getVoices, removeVoiceSample, retrainVoice } from './voices'

function reply(body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }))
  return { mock, f: mock as unknown as typeof fetch }
}

afterEach(() => resetPcModeForTests())

describe('voices api', () => {
  it('lists voices', async () => {
    const { mock, f } = reply({ items: [] })
    await getVoices(f)
    expect(mock.mock.calls[0][0]).toBe('/api/voices')
  })

  it('clear, retrain and remove-sample confirm and carry the PC-only header', async () => {
    const { mock, f } = reply({ series_id: 1, character_id: 2, sample_count: 0 })
    await clearVoice(1, 2, f)
    await retrainVoice(1, 2, f)
    await removeVoiceSample(1, 2, 7, 'SPEAKER_00', f)
    const calls = mock.mock.calls
    expect(calls.map((c) => c[0])).toEqual([
      '/api/voices/1/2/clear', '/api/voices/1/2/retrain', '/api/voices/1/2/remove-sample',
    ])
    expect(JSON.parse(calls[0][1].body)).toEqual({ confirm: true })
    expect(JSON.parse(calls[2][1].body)).toEqual({ drama_id: 7, speaker_label: 'SPEAKER_00', confirm: true })
    for (const c of calls) expect(new Headers(c[1].headers).get('X-Baihe-Local')).toBe('1')
  })
})
