import { describe, expect, it, vi } from 'vitest'

import {
  MAX_CHANNEL_INPUT,
  addFollow,
  autoStartBlocker,
  checkAllFollows,
  checkChannelInput,
  checkedText,
  describeFollowError,
  followOptions,
  followStatusText,
  optionsSummary,
  removeFollow,
  startFromFollow,
} from './liveFollows'
import { ApiError } from './client'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'

const okFetch = (body: unknown) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))

describe('checkChannelInput', () => {
  it('accepts YouTube links and @handles', () => {
    for (const t of ['@name', 'https://www.youtube.com/@name', 'youtube.com/@name', 'https://m.youtube.com/channel/UCx']) {
      expect(checkChannelInput(t)).toBeNull()
    }
  })
  it('explains what is missing or wrong', () => {
    expect(checkChannelInput('  ')).toMatch(/Still needed/)
    expect(checkChannelInput('@ab')).toMatch(/3 characters/)
    expect(checkChannelInput('https://evil.example/@x')).toMatch(/Only YouTube/)
    expect(checkChannelInput('https://www.youtube.com.evil.example/@x')).toMatch(/Only YouTube/)
    expect(checkChannelInput('x'.repeat(MAX_CHANNEL_INPUT + 1))).toMatch(/too long/)
  })
})

describe('status text', () => {
  it('says plainly what the last check found', () => {
    expect(followStatusText({ state: '', last_status: '', starts_at: null, last_checked_at: null })).toBe('Not checked yet')
    expect(followStatusText({ state: 'live', last_status: 'Live now', starts_at: null, last_checked_at: 1 })).toBe('Live now')
    expect(followStatusText({ state: 'live', last_status: 'Live now', starts_at: null, last_checked_at: 1, stream_title: 'Chat' })).toBe('Live now: Chat')
    expect(followStatusText({ state: 'upcoming', last_status: 'Scheduled', starts_at: 1_800_000_000, last_checked_at: 1 }))
      .toMatch(/^Starts at \d/)
    expect(followStatusText({ state: 'upcoming', last_status: 'Scheduled', starts_at: null, last_checked_at: 1 })).toBe('Scheduled')
    expect(followStatusText({ state: 'error', last_status: 'YouTube asked for a sign-in check.', starts_at: null, last_checked_at: 1 }))
      .toBe('YouTube asked for a sign-in check.')
    expect(checkedText(null)).toBe('Not checked yet')
    expect(checkedText(1000, 1030)).toBe('Checked just now')
    expect(checkedText(1000, 1000 + 600)).toBe('Checked 10 min ago')
    expect(checkedText(1000, 1000 + 7200)).toBe('Checked 2 h ago')
  })
  it('maps refusals to plain words', () => {
    expect(describeFollowError(new ApiError(403, { code: 'forbidden', message: 'x' }))).toMatch(/import from a link/)
    expect(describeFollowError(new ApiError(429, { code: 'rate_limited', message: 'x' }))).toMatch(/less than a minute/)
    expect(describeFollowError(new ApiError(409, { code: 'conflict', message: 'You already follow that channel.' })))
      .toMatch(/already follow/)
  })
})

describe('requests', () => {
  it('post to the follow routes and refuse a bad id without a request', async () => {
    const f = okFetch({ id: 3 })
    await addFollow({ channel: '@name' }, f as unknown as typeof fetch)
    expect((f.mock.calls[0] as unknown[])[0]).toBe('/api/live/follows')
    await removeFollow(3, f as unknown as typeof fetch)
    expect((f.mock.calls[1] as unknown[])[0]).toBe('/api/live/follows/3/remove')
    await startFromFollow(3, f as unknown as typeof fetch)
    expect((f.mock.calls[2] as unknown[])[0]).toBe('/api/live/follows/3/start')
    await checkAllFollows(f as unknown as typeof fetch)
    expect((f.mock.calls[3] as unknown[])[0]).toBe('/api/live/follows/check-all')
    await expect(removeFollow(0, f as unknown as typeof fetch)).rejects.toMatchObject({ status: 404 })
    expect(f).toHaveBeenCalledTimes(4)
  })
  it('explains why the automatic start is not offered without the hourly check', () => {
    expect(autoStartBlocker({ auto_check: false })).toMatch(/Check every hour/)
    expect(autoStartBlocker({ auto_check: true })).toBeNull()
  })
  it('keeps whole minutes and only the follow options', () => {
    const o = followOptions({
      url: 'https://x', source_language: 'ja', whisper_size: 'tiny', segment_seconds: 4, overlap_seconds: 1,
      engine: null, model: 'gemma4:12b', max_minutes: 12.6, use_gpu: true, reply_without_thinking: true,
    })
    expect(o).toEqual({ source_language: 'ja', whisper_size: 'tiny', engine: null, model: 'gemma4:12b', max_minutes: 13, use_gpu: true, glossary_drama_id: null, series_id: null })
    expect(optionsSummary(o)).toBe('ja · Ollama gemma4:12b · Whisper tiny · GPU · stop after 13 min')
    expect(renderToStaticMarkup(createElement('span', null, optionsSummary(o)))).not.toContain('http')
  })
})
