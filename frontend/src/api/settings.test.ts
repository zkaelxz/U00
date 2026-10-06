import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { getPcMode, resetPcModeForTests } from './pcOnly'
import {
  TOGGLES,
  buildUpdate,
  clampGpuMaxParallel,
  clearEndpointUrl,
  getSettings,
  gpuMaxParallelHelp,
  setEndpointUrl,
  updateGpuMaxParallel,
  updatePreferences,
  updateSetting,
  resetMonthCounter,
  undoMonthCounterReset,
} from './settings'

const overview = {
  engine_keys: { gemini: true },
  gpu_limit_enabled: false,
  gpu_max_parallel: 1,
  notify_on_completion: true,
  use_gpu: false,
  gemini_free_tier: false,
  bulk_auto_resume: false,
}
const ok = (body: unknown, status = 200) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch

describe('settings api', () => {
  it('builds a body with only the one boolean', () => {
    expect(buildUpdate('use_gpu', true)).toEqual({ use_gpu: true })
    expect(TOGGLES.map((t) => t.key).sort()).toEqual([
      'bulk_auto_resume',
      'gpu_limit_enabled',
      'notify_on_completion',
      'use_gpu',
    ])
  })

  it('has an opt-in toggle for resuming translation batches, sent as one boolean', async () => {
    expect(TOGGLES.find((t) => t.key === 'bulk_auto_resume')?.label).toBe('Resume batches on start')
    expect(buildUpdate('bulk_auto_resume', true)).toEqual({ bulk_auto_resume: true })
    const f = ok({ ...overview, bulk_auto_resume: true })
    await updateSetting('bulk_auto_resume', true, f)
    const [, init] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(JSON.parse(init.body)).toEqual({ bulk_auto_resume: true })
  })

  it('sends GPU jobs at once as one clamped whole number', async () => {
    expect([0, 1, 2.6, 9, Number.NaN].map(clampGpuMaxParallel)).toEqual([1, 1, 3, 4, 1])
    const f = ok({ ...overview, gpu_max_parallel: 4 })
    await updateGpuMaxParallel(7, f)
    const [, init] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(JSON.parse(init.body)).toEqual({ gpu_max_parallel: 4 })
  })

  it('mentions OLLAMA_NUM_PARALLEL only when more than one GPU job may run', () => {
    expect(gpuMaxParallelHelp(1)).not.toContain('OLLAMA_NUM_PARALLEL')
    expect(gpuMaxParallelHelp(2)).toContain('OLLAMA_NUM_PARALLEL')
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
    expect(new Headers(init.headers).get('X-Baihe-Local')).toBe('1')
  })

  it('a 403 from updatePreferences marks the tab remote', async () => {
    resetPcModeForTests()
    const f = ok({ error: { code: 'local_only', message: 'PC only' } }, 403)
    await expect(updatePreferences({ default_locale: 'en-GB' }, f)).rejects.toBeInstanceOf(ApiError)
    expect(getPcMode()).toBe('remote')
    resetPcModeForTests()
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

describe('month counter reset API', () => {
  it('posts reset and undo with the PC-only header', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const status = { month_spend_usd: 5, month_spend_counted_usd: 0, month_spend_reset_at: '2026-10-05T10:00:00' }
    const f = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init })
      return new Response(JSON.stringify({ before: status, after: status }), { status: 200 })
    }) as typeof fetch
    await resetMonthCounter(f)
    await undoMonthCounterReset(f)
    expect(calls.map((c) => c.url)).toEqual(['/api/settings/month-counter/reset', '/api/settings/month-counter/undo'])
    expect(calls.every((c) => c.init?.method === 'POST')).toBe(true)
  })
})
