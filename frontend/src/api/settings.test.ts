import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import {
  TOGGLES,
  buildUpdate,
  clearEndpointUrl,
  getSettings,
  setEndpointUrl,
  updatePreferences,
  updateSetting,
} from './settings'

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

  it('POSTs only the preference patch it is given', async () => {
    const f = ok(overview)
    await updatePreferences({ default_locale: 'en-GB', monthly_cap_usd: null }, f)
    const [url, init] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/settings')
    expect(JSON.parse(init.body)).toEqual({ default_locale: 'en-GB', monthly_cap_usd: null })
  })

  it('sets and clears an endpoint URL with confirm, the URL in the body only', async () => {
    const f = ok({ name: 'ollama_url', url: 'http://h:1', configured: true })
    await setEndpointUrl('ollama_url', 'http://h:1', f)
    const [url, init] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/settings/endpoints/ollama_url')
    expect(JSON.parse(init.body)).toEqual({ url: 'http://h:1', confirm: true })
    const g = ok({ name: 'ollama_url', url: null, configured: false })
    await clearEndpointUrl('ollama_url', g)
    const [url2, init2] = (g as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url2).toBe('/api/settings/endpoints/ollama_url/clear')
    expect(JSON.parse(init2.body)).toEqual({ confirm: true })
  })
})
