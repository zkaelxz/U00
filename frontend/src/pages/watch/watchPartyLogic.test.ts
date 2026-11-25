import { describe, expect, it } from 'vitest'

import { clockOffset, DRIFT_SECONDS, expectedPosition, movesToMatch } from './watchPartyLogic'

const room = (over: Partial<{ playing: boolean; position: number; at: number }> = {}) => ({ playing: true, position: 100, at: 1000, ...over })

describe('expectedPosition', () => {
  it('moves on with the server clock while playing', () => {
    // This phone's clock is 5 s behind the server's: server time 1010 is local 1005.
    const offset = clockOffset({ now: 1010 }, 1005_000)
    expect(offset).toBe(5)
    expect(expectedPosition(room(), offset, 1005_000)).toBe(110)
  })
  it('stays put while paused, and never runs backwards', () => {
    expect(expectedPosition(room({ playing: false }), 0, 9_999_000)).toBe(100)
    expect(expectedPosition(room(), 0, 500_000)).toBe(100)
  })
})

describe('movesToMatch', () => {
  it('leaves a phone within the drift alone', () => {
    const m = movesToMatch(room(), 0, 1000_000, { paused: false, currentTime: 100 + DRIFT_SECONDS - 0.1 })
    expect(m).toEqual({ seekTo: null, play: false, pause: false })
  })
  it('seeks a phone that is far off and starts a paused one', () => {
    const m = movesToMatch(room(), 0, 1002_000, { paused: true, currentTime: 20 })
    expect(m).toEqual({ seekTo: 102, play: true, pause: false })
  })
  it('pauses a phone that is playing when the party is paused', () => {
    expect(movesToMatch(room({ playing: false }), 0, 1000_000, { paused: false, currentTime: 100 }))
      .toEqual({ seekTo: null, play: false, pause: true })
  })
})
