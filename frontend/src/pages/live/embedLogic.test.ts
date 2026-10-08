import { describe, expect, it } from 'vitest'

import {
  DVR_WAIT_S, NOT_STARTED_NOTE, NO_DELAY_NOTE, UNREACHABLE_NOTE, WAITING_NOTE, canDelay, captionDelay, delayNote, delayReached, embedSrc, measuredDelay, notStarted, parseStreamUrl, parseYouTubeInfo, planDelay, probeOffset, ytCommand,
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
    expect(embedSrc({ kind: 'youtube', id: ID }, 'x')).toBe(`https://www.youtube-nocookie.com/embed/${ID}?enablejsapi=1&playsinline=1&autoplay=1`)
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

describe('autoplay and a player that has not started', () => {
  it('asks for autoplay in the embed address', () => {
    expect(embedSrc({ kind: 'youtube', id: 'dQw4w9WgXcQ' }, 'localhost')).toContain('&autoplay=1')
    expect(embedSrc({ kind: 'twitch-channel', name: 'abc' }, 'localhost')).not.toContain('autoplay')
  })
  it('reads the player state and keeps it across partial messages', () => {
    expect(report({ playerState: -1 })).toEqual({ playerState: -1 })
    expect({ ...report({ playerState: 1 }), ...report({ currentTime: 3 }) }).toEqual({ playerState: 1, currentTime: 3 })
  })
  it('treats unstarted and cued as not started, and a silent player as started', () => {
    expect(notStarted(report({ playerState: -1 }))).toBe(true)
    expect(notStarted(report({ playerState: 5 }))).toBe(true)
    expect(notStarted(report({ playerState: 1 }))).toBe(false)
    expect(notStarted(report({ playerState: 2 }))).toBe(false)
    expect(notStarted(null)).toBe(false)
  })
  it('says to press play rather than that the stream cannot be delayed', () => {
    expect(delayNote(report({ playerState: -1, duration: 0 }), 15, { unsupported: true, moving: false })).toBe(NOT_STARTED_NOTE)
  })
  it('builds player commands', () => {
    expect(JSON.parse(ytCommand('mute'))).toEqual({ event: 'command', func: 'mute', args: [] })
  })
})

// YouTube documents getDuration() on a live event as the time since the
// stream began, while getCurrentTime() counts from where playback started, so
// duration - currentTime is not the distance from the live edge.
describe('a live stream whose duration is the time since it began', () => {
  const stuck = report({ duration: 43826, currentTime: 20, isLive: true, playerState: 1 })
  const videoData = { isLive: true }
  it('reproduces the 12-hour reading the owner saw', () => {
    expect(measuredDelay(stuck)).toBe(43806)
  })
  it('never shows an implausible figure as a delay', () => {
    const note = delayNote(stuck, 20, { unsupported: false, moving: false })
    expect(note).not.toContain('43806')
    expect(note).toBe(UNREACHABLE_NOTE)
    expect(parseYouTubeInfo({ event: 'infoDelivery', info: { duration: 43826, videoData } })).toEqual({ duration: 43826, isLive: true })
  })
  it('measures against the offset found at the live edge', () => {
    const offset = 43800
    const info = report({ duration: 43830, currentTime: 10, playerState: 1 })
    expect(measuredDelay(info, offset)).toBe(20)
    expect(delayReached(info, 20, offset)).toBe(true)
    expect(planDelay({ duration: 43830, isLive: true }, 20, 0, offset)).toEqual({ kind: 'seek', to: 10 })
    expect(delayNote(info, 20, { unsupported: false, moving: false, offset })).toBe('Playing about 20 s behind live.')
  })
  it('finds that offset only when the probe seek really moved the playhead', () => {
    expect(probeOffset(20, report({ duration: 43830, currentTime: 14400 }))).toBe(29430)
    expect(probeOffset(20, report({ duration: 300, currentTime: 299 }))).toBe(1)
    expect(probeOffset(20, report({ duration: 43830, currentTime: 22 }))).toBeNull()
    expect(probeOffset(undefined, report({ duration: 43830, currentTime: 22 }))).toBeNull()
    expect(probeOffset(20, null)).toBeNull()
  })
})

describe('captionDelay', () => {
  const flags = { unsupported: false, moving: false, unreachable: false }
  it('is the delay the picture has once it got there', () => {
    expect(captionDelay(report({ duration: 300, currentTime: 285 }), 15, flags)).toBe(15)
  })
  it('is the clamped window when the slider asks for more than the stream keeps', () => {
    expect(captionDelay(report({ duration: 10, currentTime: 0 }), 30, flags)).toBe(10)
  })
  it('follows the measured delay when the viewer scrubs away from the target', () => {
    expect(captionDelay(report({ duration: 300, currentTime: 300 }), 15, flags)).toBe(0)
    expect(captionDelay(report({ duration: 300, currentTime: 290 }), 30, flags)).toBe(10)
  })
  it('is 0 while the picture is not yet behind live', () => {
    expect(captionDelay(null, 15, flags)).toBe(0)
    expect(captionDelay(report({ duration: 300 }), 15, flags)).toBe(0)
    expect(captionDelay(report({ duration: 300, currentTime: 285 }), 30, { ...flags, moving: true })).toBe(0)
    expect(captionDelay(report({ duration: 300, currentTime: 300, playerState: -1 }), 15, flags)).toBe(0)
  })
  it('is 0 for a stream that cannot be delayed or ignores seeks', () => {
    expect(captionDelay(report({ duration: 0 }), 15, { ...flags, unsupported: true })).toBe(0)
    expect(captionDelay(report({ duration: 300, currentTime: 300 }), 20, { ...flags, unreachable: true })).toBe(0)
    expect(captionDelay(report({ duration: 43826, currentTime: 100 }), 20, flags)).toBe(0)
  })
})
