import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../../api/client'
import { confirmStep, fetchIsLocal, pcOnlyErrorText, reportPcOnlyError } from './pcOnly'

const meta = (body: unknown, status = 200) =>
  (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch

describe('fetchIsLocal', () => {
  it('is true only when /api/meta says local: true', async () => {
    expect(await fetchIsLocal(meta({ app: 'baihe', local: true }))).toBe(true)
    expect(await fetchIsLocal(meta({ app: 'baihe', local: false }))).toBe(false)
  })
  it('treats a missing field or a failed read as not local', async () => {
    expect(await fetchIsLocal(meta({ app: 'baihe' }))).toBe(false)
    expect(await fetchIsLocal(meta({ error: { code: 'internal_error', message: 'x' } }, 500))).toBe(false)
    const down = (async () => {
      throw new TypeError('offline')
    }) as unknown as typeof fetch
    expect(await fetchIsLocal(down)).toBe(false)
  })
})

describe('confirmStep', () => {
  it('first press arms without firing; the second fires and disarms', () => {
    expect(confirmStep(false, 'press')).toEqual({ armed: true, fire: false })
    expect(confirmStep(true, 'press')).toEqual({ armed: false, fire: true })
  })
  it('timeout and cancel disarm without firing', () => {
    expect(confirmStep(true, 'timeout')).toEqual({ armed: false, fire: false })
    expect(confirmStep(true, 'cancel')).toEqual({ armed: false, fire: false })
  })
})

describe('PC-only errors', () => {
  it('gives plain text for forbidden and conflict only', () => {
    expect(pcOnlyErrorText(new ApiError(403, { code: 'forbidden', message: 'x' }))).toBe(
      'This only works on the main PC.',
    )
    expect(pcOnlyErrorText(new ApiError(409, { code: 'conflict', message: 'x' }))).toBe(
      'Wait for the running job to finish.',
    )
    expect(pcOnlyErrorText(new ApiError(404, { code: 'not_found', message: 'x' }))).toBeNull()
    expect(pcOnlyErrorText(null)).toBeNull()
  })
  it('routes other errors to the banner', () => {
    const text = vi.fn()
    const banner = vi.fn()
    const err = new ApiError(404, { code: 'not_found', message: 'gone' })
    reportPcOnlyError(err, text, banner)
    expect(text).not.toHaveBeenCalled()
    expect(banner).toHaveBeenCalledWith(err)
    reportPcOnlyError(new ApiError(403, { code: 'forbidden', message: 'x' }), text, banner)
    expect(text).toHaveBeenCalledWith('This only works on the main PC.')
  })
})
