import { describe, expect, it } from 'vitest'

import {
  DVR_WAIT_S, NO_DELAY_NOTE, WAITING_NOTE, canDelay, delayNote, delayReached, embedSrc, measuredDelay, parseStreamUrl, parseYouTubeInfo, planDelay,
} from './embedLogic'

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

describe('parseYouTubeInfo partial messages', () => {
  it('leaves out fields the message does not carry, so a merge keeps the earlier value', () => {
    const got = parseYouTubeInfo({ event: 'infoDelivery', info: { currentTime: 9 } })
    expect(Object.keys(got ?? {})).toEqual(['currentTime'])
    expect({ duration: 300, ...got }).toEqual({ duration: 300, currentTime: 9 })
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

// Fabricated player reports, as parseYouTubeInfo reads them off the wire.
const report = (info: object) => parseYouTubeInfo(JSON.stringify({ event: 'infoDelivery', info }))

describe('measuredDelay', () => {
  it('is the window end minus the playhead, rounded', () => {
    expect(measuredDelay(report({ duration: 300, currentTime: 285 }))).toBe(15)
    expect(measuredDelay(report({ duration: 300.4, currentTime: 285 }))).toBe(15)
    expect(measuredDelay(report({ duration: 300.6, currentTime: 285 }))).toBe(16)
  })
  it('is null until both numbers are known, and never negative', () => {
    expect(measuredDelay(null)).toBeNull()
    expect(measuredDelay(report({ duration: 300 }))).toBeNull()
    expect(measuredDelay(report({ currentTime: 3 }))).toBeNull()
    expect(measuredDelay(report({ duration: 0, currentTime: 0 }))).toBeNull()
    expect(measuredDelay(report({ duration: 300, currentTime: 301 }))).toBe(0)
  })
})

describe('delayReached', () => {
  it('is true within the tolerance of the wanted delay', () => {
    expect(delayReached(report({ duration: 300, currentTime: 285 }), 15)).toBe(true)
    expect(delayReached(report({ duration: 300, currentTime: 283 }), 15)).toBe(true)
    expect(delayReached(report({ duration: 300, currentTime: 290 }), 15)).toBe(false)
    expect(delayReached(null, 15)).toBe(false)
  })
  it('counts a seek clamped to the window start as reached', () => {
    expect(delayReached(report({ duration: 45, currentTime: 0 }), 90)).toBe(true)
  })
})

describe('delayNote', () => {
  const flags = { unsupported: false, moving: false }
  it('waits for the player before it says anything about delay', () => {
    expect(delayNote(null, 15, flags)).toBe(WAITING_NOTE)
    expect(WAITING_NOTE).toBe('Waiting for the player…')
    expect(delayNote(report({ duration: 300 }), 15, flags)).toBe(WAITING_NOTE)
  })
  it('reports the measured delay, not the slider', () => {
    expect(delayNote(report({ duration: 300, currentTime: 285 }), 15, flags)).toBe('Playing about 15 s behind live.')
    // The slider says 60 but the player has not moved: say what it is doing.
    expect(delayNote(report({ duration: 300, currentTime: 290 }), 60, flags)).toBe('Playing about 10 s behind live.')
  })
  it('says the stream clamped the seek', () => {
    expect(delayNote(report({ duration: 45.5, currentTime: 0 }), 90, flags))
      .toBe('Playing about 46 s behind live (this stream allows at most 45 s).')
    expect(delayNote(report({ duration: 45, currentTime: 0 }), 45, flags)).toBe('Playing about 45 s behind live.')
  })
  it('answers the slider at once while a seek is in flight', () => {
    expect(delayNote(report({ duration: 300, currentTime: 285 }), 30, { unsupported: false, moving: true })).toBe('Moving to about 30 s behind live…')
    expect(delayNote(report({ duration: 45, currentTime: 45 }), 90, { unsupported: false, moving: true })).toBe('Moving to about 45 s behind live…')
  })
  it('says a stream with no rewind buffer cannot be delayed', () => {
    expect(delayNote(report({ duration: 0 }), 15, { unsupported: true, moving: false })).toBe(NO_DELAY_NOTE)
  })
})
