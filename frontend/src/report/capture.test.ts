import { afterEach, describe, expect, it, vi } from 'vitest'

import { getJson, postJson } from '../api/client'
import {
  BUFFER_SIZE, ROUTE_HISTORY_SIZE, Ring, captureSnapshot, describeArg, installCapture, recordFailedRequest,
  recordRoute, resetCaptureForTests, routeFromHash, sanitize, stripUrl,
} from './capture'

afterEach(() => resetCaptureForTests())

function fakeWindow(hash = '#/library') {
  const target = new EventTarget()
  return Object.assign(target, { location: { hash } })
}

describe('capture buffers', () => {
  it('Ring keeps only the newest items', () => {
    const r = new Ring<number>(3)
    for (let i = 0; i < 5; i++) r.push(i)
    expect(r.list()).toEqual([2, 3, 4])
  })

  it('records console errors/warnings with strings and Error text only', () => {
    const calls: unknown[][] = []
    const con = { error: (...a: unknown[]) => calls.push(a), warn: (...a: unknown[]) => calls.push(a) }
    installCapture(fakeWindow(), con)
    con.error('Failed:', new TypeError('x is undefined'), { line: 'secret line text', body: 'resp' })
    con.warn('slow', 42)
    const snap = captureSnapshot()
    expect(snap.console.map((c) => [c.level, c.message])).toEqual([
      ['error', 'Failed: TypeError: x is undefined [object]'],
      ['warn', 'slow 42'],
    ])
    expect(JSON.stringify(snap)).not.toContain('secret line text')
    expect(calls).toHaveLength(2)   // still forwarded to the real console
  })

  it('keeps at most BUFFER_SIZE entries per buffer', () => {
    const con = { error: (..._a: unknown[]) => undefined, warn: (..._a: unknown[]) => undefined }
    installCapture(fakeWindow(), con)
    for (let i = 0; i < BUFFER_SIZE + 5; i++) con.error(`e${i}`)
    const list = captureSnapshot().console
    expect(list).toHaveLength(BUFFER_SIZE)
    expect(list[0].message).toBe('e5')
  })

  it('records window errors and unhandled rejections', () => {
    const win = fakeWindow()
    installCapture(win, { error: () => undefined, warn: () => undefined })
    const err = Object.assign(new Event('error'), {
      message: 'boom', filename: 'http://127.0.0.1:5173/assets/index-AbC.js?v=1', lineno: 3, colno: 9,
      error: new RangeError('bad index'),
    })
    win.dispatchEvent(err)
    win.dispatchEvent(Object.assign(new Event('unhandledrejection'), { reason: { secret: 'x' } }))
    const errors = captureSnapshot().errors
    expect(errors[0]).toMatchObject({ kind: 'error', message: 'RangeError: bad index', source: '/assets/index-AbC.js' })
    expect(errors[1]).toMatchObject({ kind: 'unhandledrejection', message: '[object]' })
  })

  it('tracks the route and the last route changes without query strings', () => {
    const win = fakeWindow('#/drama/3/review')
    installCapture(win, { error: () => undefined, warn: () => undefined })
    win.location.hash = '#/read/3?page=2'
    win.dispatchEvent(new Event('hashchange'))
    for (let i = 0; i < ROUTE_HISTORY_SIZE + 2; i++) recordRoute(`#/drama/${i}`)
    const snap = captureSnapshot()
    expect(snap.route).toBe(`/drama/${ROUTE_HISTORY_SIZE + 1}`)
    expect(snap.route_history).toHaveLength(ROUTE_HISTORY_SIZE)
    expect(routeFromHash('#/read/3?page=2')).toBe('/read/3')
  })

  it('installCapture is idempotent', () => {
    const con = { error: vi.fn(), warn: vi.fn() }
    installCapture(fakeWindow(), con)
    const wrapped = con.error
    installCapture(fakeWindow(), con)
    expect(con.error).toBe(wrapped)
  })
})

describe('failed API calls', () => {
  it('stripUrl drops origin, query and hash', () => {
    expect(stripUrl('/api/x/y?key=sk-abcdefghijklmnop#h')).toBe('/api/x/y')
    expect(stripUrl('http://127.0.0.1:8600/api/jobs?x=1')).toBe('/api/jobs')
  })

  it('the shared client records method, path, status and code, never bodies or headers', async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'not_found', message: 'No title "My secret title".' },
    }), { status: 404 })) as unknown as typeof fetch
    await expect(postJson('/api/review/dramas/3/lines?q=secret', { text: 'line text body' }, f)).rejects.toThrow()
    const down = vi.fn().mockRejectedValue(new TypeError('Failed to fetch')) as unknown as typeof fetch
    await expect(getJson('/api/jobs', down)).rejects.toThrow()
    const ok = vi.fn().mockResolvedValue(new Response('{}', { status: 200 })) as unknown as typeof fetch
    await getJson('/api/health', ok)
    const failed = captureSnapshot().failed_requests
    expect(failed.map(({ method, path, status, code }) => ({ method, path, status, code }))).toEqual([
      { method: 'POST', path: '/api/review/dramas/3/lines', status: 404, code: 'not_found' },
      { method: 'GET', path: '/api/jobs', status: 0, code: 'network_error' },
    ])
    const text = JSON.stringify(failed)
    for (const bad of ['secret', 'line text', 'X-Baihe-Local', 'My secret title']) expect(text).not.toContain(bad)
  })

  it('does not record aborted requests or a busy waveform (429 from peaks)', async () => {
    const aborted = vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError')) as unknown as typeof fetch
    await expect(getJson('/api/media/dramas/3/peaks', aborted)).rejects.toThrow()
    const busy = vi.fn(async () => new Response(JSON.stringify({ error: { code: 'rate_limited', message: 'busy' } }), { status: 429 })) as unknown as typeof fetch
    await expect(getJson('/api/media/dramas/3/peaks', busy)).rejects.toThrow()
    expect(captureSnapshot().failed_requests).toEqual([])
    await expect(getJson('/api/other', busy)).rejects.toThrow()
    expect(captureSnapshot().failed_requests).toHaveLength(1)
  })

  it('recordFailedRequest normalises the method', () => {
    recordFailedRequest('delete', '/api/dramas/3', 403, 'forbidden')
    expect(captureSnapshot().failed_requests[0]).toMatchObject({ method: 'DELETE', status: 403 })
  })
})

describe('sanitize and describeArg', () => {
  it('masks keys, cookies, CSRF and long tokens and cuts long text', () => {
    const out = sanitize('k=sk-ant-api03-SECRETSECRET1234 baihe_session=abc123; X-CSRF-Token: tok123 '
      + 'AIzaSyA1234567890abcdef Bearer ' + 'y'.repeat(40))
    for (const bad of ['SECRETSECRET', 'abc123', 'tok123', 'AIzaSy', 'yyyyyyyy']) expect(out).not.toContain(bad)
    expect(sanitize('ab '.repeat(200))).toHaveLength(501)
  })

  it('describeArg never serialises objects', () => {
    expect(describeArg({ a: 1 })).toBe('[object]')
    expect(describeArg([1])).toBe('[array]')
    expect(describeArg(null)).toBe('null')
  })
})
