import { describe, expect, it } from 'vitest'

import { ApiError } from './client'
import { mediaStreamUrl } from './media'
import * as rs from './restructure'

type Call = { url: string; init?: RequestInit }

function fakeFetch(responses: { status: number; body: unknown }[], calls: Call[] = []) {
  let i = 0
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    const r = responses[Math.min(i++, responses.length - 1)]
    return new Response(JSON.stringify(r.body), { status: r.status })
  }) as typeof fetch
}

const ok = (body: unknown) => ({ status: 200, body })
const body = (c: Call) => JSON.parse(String(c.init?.body))
const result = { line_ids: [1, 2], lines: [] }

describe('restructure api', () => {
  it('adds a line with the expected ids', async () => {
    const calls: Call[] = []
    await rs.addLine(3, { expected_line_ids: [1], after_line_id: 1, start: 1, end: 2, zh: 'a' }, fakeFetch([ok(result)], calls))
    expect(calls[0].url).toBe('/api/restructure/dramas/3/lines/add')
    expect(calls[0].init?.method).toBe('POST')
    expect(body(calls[0])).toEqual({ expected_line_ids: [1], after_line_id: 1, start: 1, end: 2, zh: 'a' })
  })

  it('deletes with confirm=true', async () => {
    const calls: Call[] = []
    await rs.deleteLine(3, 9, [8, 9], fakeFetch([ok(result)], calls))
    expect(calls[0].url).toBe('/api/restructure/dramas/3/lines/9/delete')
    expect(body(calls[0])).toEqual({ expected_line_ids: [8, 9], confirm: true })
  })

  it('merges named lines', async () => {
    const calls: Call[] = []
    await rs.mergeLines(3, [4, 5], [4, 5, 6], fakeFetch([ok(result)], calls))
    expect(calls[0].url).toBe('/api/restructure/dramas/3/merge')
    expect(body(calls[0])).toEqual({ line_ids: [4, 5], expected_line_ids: [4, 5, 6] })
  })

  it('splits and leaves out unset optional fields', async () => {
    const calls: Call[] = []
    const f = fakeFetch([ok(result)], calls)
    await rs.splitLine(3, 4, { expected_line_ids: [4], at_char: 2, expected_zh: '你好吗' }, f)
    expect(calls[0].url).toBe('/api/restructure/dramas/3/lines/4/split')
    expect(body(calls[0])).toEqual({ expected_line_ids: [4], at_char: 2, expected_zh: '你好吗' })
    await rs.splitLine(3, 4, { expected_line_ids: [4], at_char: 2, expected_zh: '你好吗', at_time: 1.5, en_at_char: 3 }, f)
    expect(body(calls[1])).toMatchObject({ at_time: 1.5, en_at_char: 3 })
  })

  it('sends engine and model only when AI re-segmentation is on', async () => {
    const calls: Call[] = []
    const f = fakeFetch([ok({ job_id: 'resegment_3', drama_id: 3 })], calls)
    await rs.startResegment(3, { expected_line_ids: [1], confirm: true, use_llm: false, engine: 'x', model: 'y' }, f)
    expect(body(calls[0])).toEqual({ expected_line_ids: [1], confirm: true, use_llm: false })
    await rs.startResegment(3, { expected_line_ids: [1], confirm: true, use_llm: true, engine: 'x', model: '' }, f)
    expect(body(calls[1])).toEqual({ expected_line_ids: [1], confirm: true, use_llm: true, engine: 'x' })
  })

  it('reads the preview and restores a snapshot', async () => {
    const calls: Call[] = []
    const f = fakeFetch([ok({}), ok({ history_id: 2, line_ids: [1] })], calls)
    await rs.previewResegment(3, f)
    await rs.restoreSnapshot(3, 2, [1, 2], f)
    expect(calls[0].url).toBe('/api/restructure/dramas/3/resegment/preview')
    expect(calls[1].url).toBe('/api/restructure/dramas/3/history/2/restore')
    expect(body(calls[1])).toEqual({ expected_line_ids: [1, 2] })
  })

  it('pages through every line in order', async () => {
    const calls: Call[] = []
    const line = (id: number) => ({ id })
    const pageOf = (ids: number[]) => ok({ lines: ids.map(line), total: 3, page: 1, page_size: 200, flagged_count: 0, untranslated_count: 0 })
    const all = await rs.listAllLines(3, fakeFetch([pageOf([1, 2]), pageOf([3])], calls))
    expect(all.map((l) => l.id)).toEqual([1, 2, 3])
    expect(calls.map((c) => c.url)).toEqual([
      '/api/review/dramas/3/lines?page=1&page_size=200&only=all',
      '/api/review/dramas/3/lines?page=2&page_size=200&only=all',
    ])
  })

  it('surfaces a line-mismatch 409 as an ApiError', async () => {
    const f = fakeFetch([{ status: 409, body: { error: { code: 'conflict', message: 'changed' } } }])
    await expect(rs.mergeLines(3, [1, 2], [1, 2], f)).rejects.toBeInstanceOf(ApiError)
  })
})

describe('media stream url', () => {
  it('points at the Range endpoints', () => {
    expect(mediaStreamUrl(4, 'audio')).toBe('/api/media/dramas/4/audio')
    expect(mediaStreamUrl(4, 'video')).toBe('/api/media/dramas/4/video')
  })
})
