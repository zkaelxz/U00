import { describe, expect, it } from 'vitest'

import { DVR_WAIT_S, canDelay, embedSrc, parseStreamUrl, parseYouTubeInfo, planDelay } from './streamEmbed'

const ID = 'dQw4w9WgXcQ'

describe('parseStreamUrl', () => {
  it.each([
    `https://www.youtube.com/watch?v=${ID}`,
    `https://youtube.com/watch?v=${ID}&t=5`,
    `https://m.youtube.com/watch?v=${ID}`,
    `https://youtu.be/${ID}?si=x`,
    `https://www.youtube.com/live/${ID}`,
    `https://www.youtube.com/embed/${ID}`,
    `  http://www.youtube.com/watch?v=${ID}  `,
  ])('accepts %s', (u) => {
    expect(parseStreamUrl(u)).toEqual({ kind: 'youtube', id: ID })
  })

  it('accepts twitch channels and videos', () => {
    expect(parseStreamUrl('https://www.twitch.tv/some_Name1')).toEqual({ kind: 'twitch-channel', name: 'some_Name1' })
    expect(parseStreamUrl('https://twitch.tv/videos/123456')).toEqual({ kind: 'twitch-video', id: '123456' })
  })

  it.each([
    `https://youtube.com.evil.test/watch?v=${ID}`,
    `https://evil.test/youtu.be/${ID}`,
    `https://notyoutu.be/${ID}`,
    `https://youtube.com@evil.test/watch?v=${ID}`,
    `javascript:alert(1)//youtube.com/watch?v=${ID}`,
    `data:text/html,${ID}`,
    `ftp://www.youtube.com/watch?v=${ID}`,
    'https://www.youtube.com/watch?v=short',
    `https://www.youtube.com/watch?v=${ID}x`,
    'https://www.youtube.com/watch?v=abc"onload=1',
    'https://www.youtube.com/results?search_query=abc',
    'https://www.twitch.tv.evil.test/name',
    `https://www.twitch.tv/${'a'.repeat(26)}`,
    'https://www.twitch.tv/a/b',
    'https://www.bilibili.com/video/BV1xx',
    'not a url',
    '',
  ])('rejects %s', (u) => {
    expect(parseStreamUrl(u)).toBeNull()
  })
})

describe('embedSrc', () => {
  it('builds the address from the validated parts only', () => {
    expect(embedSrc({ kind: 'youtube', id: ID }, 'x')).toBe(`https://www.youtube-nocookie.com/embed/${ID}?enablejsapi=1&playsinline=1`)
    expect(embedSrc({ kind: 'twitch-channel', name: 'abc' }, 'localhost')).toBe('https://player.twitch.tv/?channel=abc&parent=localhost')
    expect(embedSrc({ kind: 'twitch-video', id: '9' }, 'h.test')).toBe('https://player.twitch.tv/?video=v9&parent=h.test')
  })
  it('only YouTube can be delayed', () => {
    expect(canDelay({ kind: 'youtube', id: ID })).toBe(true)
    expect(canDelay({ kind: 'twitch-channel', name: 'a' })).toBe(false)
  })
})

describe('parseYouTubeInfo', () => {
  it('reads infoDelivery', () => {
    expect(parseYouTubeInfo(JSON.stringify({ event: 'infoDelivery', info: { currentTime: 3, duration: 120 } }))).toEqual({ currentTime: 3, duration: 120 })
  })
  it('ignores everything else', () => {
    expect(parseYouTubeInfo('nope')).toBeNull()
    expect(parseYouTubeInfo({ event: 'onReady' })).toBeNull()
    expect(parseYouTubeInfo(null)).toBeNull()
    expect(parseYouTubeInfo({ event: 'infoDelivery', info: { duration: 'x' } })).toEqual({ currentTime: undefined, duration: undefined })
    expect(parseYouTubeInfo({ event: 'infoDelivery', info: { duration: 5, videoData: { isLive: false } } })).toEqual({ currentTime: undefined, duration: 5, isLive: false })
  })
})

describe('planDelay', () => {
  it('seeks to duration minus delay', () => {
    expect(planDelay({ duration: 300 }, 15, 0)).toEqual({ kind: 'seek', to: 285 })
    expect(planDelay({ duration: 300.7 }, 0, 0)).toEqual({ kind: 'seek', to: 300 })
  })
  it('clamps the delay and the target', () => {
    expect(planDelay({ duration: 300 }, 500, 0)).toEqual({ kind: 'seek', to: 210 })
    expect(planDelay({ duration: 20 }, 60, 0)).toEqual({ kind: 'seek', to: 0 })
  })
  it('does not seek a finished video', () => {
    expect(planDelay({ duration: 3000, isLive: false }, 15, 0)).toEqual({ kind: 'unsupported' })
  })
  it('waits, then gives up when there is no seekable window', () => {
    expect(planDelay(null, 15, 0)).toEqual({ kind: 'wait' })
    expect(planDelay({ duration: 0 }, 15, DVR_WAIT_S - 1)).toEqual({ kind: 'wait' })
    expect(planDelay({ duration: 0 }, 15, DVR_WAIT_S)).toEqual({ kind: 'unsupported' })
    expect(planDelay({}, 15, 99)).toEqual({ kind: 'unsupported' })
  })
})
