import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { TOGGLES, buildUpdate, getSettings, updateSetting } from './settings'

const overview = {
  engine_keys: { gemini: true },
  gpu_limit_enabled: false,
  notify_on_completion: true,
  use_gpu: false,
  gemini_free_tier: false,
}
const ok = (body: unknown, status = 200) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch

describe('settings api', () => {
  it('builds a body with only the one boolean', () => {
    expect(buildUpdate('use_gpu', true)).toEqual({ use_gpu: true })
    expect(TOGGLES.map((t) => t.key).sort()).toEqual([
      'gemini_free_tier',
      'gpu_limit_enabled',
      'notify_on_completion',
      'use_gpu',
    ])
  })

  it('POSTs that body and returns the overview', async () => {
    const f = ok(overview)
    expect(await updateSetting('notify_on_completion', false, f)).toEqual(overview)
    const [url, init] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/settings')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ notify_on_completion: false })
  })

  it('GETs the overview and surfaces API errors', async () => {
    expect(await getSettings(ok(overview))).toEqual(overview)
    await expect(
      updateSetting('use_gpu', true, ok({ error: { code: 'invalid_input', message: 'no' } }, 422)),
    ).rejects.toBeInstanceOf(ApiError)
  })
})
