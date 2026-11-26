import { describe, expect, it, vi } from 'vitest'

import {
  MAX_CHANNEL_INPUT,
  addFollow,
  autoStartBlocker,
  checkAllFollows,
  checkChannelInput,
  checkChannelsInput,
  splitChannels,
  MAX_CHANNELS_PER_ADD,
  channelPageUrl,
  checkedText,
  describeFollowError,
  parseTerms,
  termsToText,
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
    for (const t of ['@name', 'https://www.youtube.com/@name', 'youtube.com/@name', 'https://m.youtube.com/channel/UCx', 'https://www.twitch.tv/name', 'twitch.tv/name']) {
      expect(checkChannelInput(t)).toBeNull()
    }
  })
  it('explains what is missing or wrong', () => {
    expect(checkChannelInput('  ')).toMatch(/Still needed/)
    expect(checkChannelInput('@ab')).toMatch(/3 characters/)
    expect(checkChannelInput('https://evil.example/@x')).toMatch(/Only YouTube and Twitch/)
    expect(checkChannelInput('https://www.youtube.com.evil.example/@x')).toMatch(/Only YouTube and Twitch/)
    expect(checkChannelInput('x'.repeat(MAX_CHANNEL_INPUT + 1))).toMatch(/too long/)
  })
})

describe('several channels at once', () => {
  it('splits on lines, spaces and commas and drops repeats', () => {
    expect(splitChannels(' @one\n\n@two, https://www.twitch.tv/three @one ')).toEqual(['@one', '@two', 'https://www.twitch.tv/three'])
  })
  it('names the first bad line, and caps how many', () => {
    expect(checkChannelsInput('@one\n@two')).toBeNull()
    expect(checkChannelsInput('@one\nhttps://evil.example/@x')).toMatch(/^https:\/\/evil\.example\/@x: Only YouTube/)
    expect(checkChannelsInput('@ab')).toMatch(/3 characters/)
    expect(checkChannelsInput('\n ')).toMatch(/Still needed/)
    const many = Array.from({ length: MAX_CHANNELS_PER_ADD + 1 }, (_, i) => `@chan${i}`).join('\n')
    expect(checkChannelsInput(many)).toMatch(/Up to/)
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
    expect(describeFollowError(new ApiError(409, { code: 'conflict', message: 'Channel has already been added.' })))
      .toBe('Channel has already been added.')
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

describe('channelPageUrl', () => {
  it('rebuilds the channel page from the short label', () => {
    expect(channelPageUrl('@name')).toBe('https://www.youtube.com/@name')
    expect(channelPageUrl('channel/UCabc_-123')).toBe('https://www.youtube.com/channel/UCabc_-123')
    expect(channelPageUrl('twitch/some_one')).toBe('https://www.twitch.tv/some_one')
  })

  it('gives nothing for a follow with no valid address', () => {
    expect(channelPageUrl('invalid')).toBeNull()
    expect(channelPageUrl('')).toBeNull()
  })

  it('keeps a hostile label inside the page address', () => {
    expect(channelPageUrl('@a b?x=1#y')).toBe('https://www.youtube.com/@a%20b%3Fx%3D1%23y')
  })
})

describe('a channel word list', () => {
  it('round-trips through the box text', () => {
    const { terms, error } = parseTerms('云朵 = Yunduo\n\n阿洛＝RLuo')
    expect(error).toBeNull()
    expect(terms).toEqual([{ original: '云朵', translation: 'Yunduo' }, { original: '阿洛', translation: 'RLuo' }])
    expect(termsToText(terms)).toBe('云朵 = Yunduo\n阿洛 = RLuo')
  })
  it('names the line that cannot be saved', () => {
    expect(parseTerms('云朵').error).toMatch(/Line 1/)
    expect(parseTerms('a = b\n= c').error).toMatch(/Line 2/)
    expect(parseTerms(`${'x'.repeat(31)} = y`).error).toMatch(/30/)
  })
})
