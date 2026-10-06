import { describe, expect, it } from 'vitest'

import { clampTime, lineAt, parseJumpTime, PLAYBACK_RATES, popoutCaptionPx, SUBTITLE_OPTIONS, subtitleSrc, validRate } from './playerLogic'

describe('parseJumpTime', () => {
  it('reads seconds, mm:ss and h:mm:ss', () => {
    expect(parseJumpTime('83.5')).toBe(83.5)
    expect(parseJumpTime(' 1:23 ')).toBe(83)
    expect(parseJumpTime('1:02:03')).toBe(3723)
    expect(parseJumpTime('0:05.25')).toBe(5.25)
    expect(parseJumpTime('90:00')).toBe(5400)
  })
  it('rejects anything else', () => {
    for (const bad of ['', 'abc', '1:', ':30', '1:75', '1:2:3:4', '-3', '1.5:20', '1e3']) {
      expect(parseJumpTime(bad)).toBeNull()
    }
  })
})

describe('clampTime', () => {
  it('keeps the time inside the media', () => {
    expect(clampTime(-2, 10)).toBe(0)
    expect(clampTime(12, 10)).toBe(10)
    expect(clampTime(12, NaN)).toBe(12)
  })
})

describe('subtitleSrc', () => {
  it('points at the caption route with a version', () => {
    expect(subtitleSrc(3, 'English', 4)).toBe('/api/reader/dramas/3/captions/English?v=4')
    expect(subtitleSrc(3, 'Source', 0)).toBe('/api/reader/dramas/3/captions/Source?v=0')
    expect(subtitleSrc(3, 'off', 1)).toBeNull()
  })
  it('offers "Both" as the bilingual track', () => {
    expect(SUBTITLE_OPTIONS.find((o) => o.label === 'Both')?.value).toBe('Bilingual')
    expect(subtitleSrc(3, 'Bilingual', 2)).toBe('/api/reader/dramas/3/captions/Bilingual?v=2')
  })
})

describe('lineAt', () => {
  const lines = [
    { id: 1, start: 0, end: 1.5 },
    { id: 2, start: 1.5, end: 3 },
    { id: 3, start: 4, end: 5 },
  ]
  it('finds the line under the playhead', () => {
    expect(lineAt(lines, 0)?.id).toBe(1)
    expect(lineAt(lines, 1.5)?.id).toBe(2)
    expect(lineAt(lines, 3.5)).toBeNull()
    expect(lineAt(lines, 4.9)?.id).toBe(3)
    expect(lineAt([], 1)).toBeNull()
  })
})

describe('popoutCaptionPx', () => {
  it('is about 2.25% of a wide window', () => {
    expect(popoutCaptionPx(2000)).toBe(45)
  })
  it('never drops below the inline size', () => {
    expect(popoutCaptionPx(300)).toBe(16)
    expect(popoutCaptionPx(0)).toBe(16)
    expect(popoutCaptionPx(NaN)).toBe(16)
  })
  it('is capped on huge windows', () => {
    expect(popoutCaptionPx(10000, 'larger')).toBe(72)
  })
  it('scales with the size choice', () => {
    expect(popoutCaptionPx(2000, 'smaller')).toBeLessThan(popoutCaptionPx(2000))
    expect(popoutCaptionPx(2000, 'larger')).toBeGreaterThan(popoutCaptionPx(2000))
  })
})

describe('validRate', () => {
  it('keeps listed speeds and falls back to normal speed for anything else', () => {
    for (const r of PLAYBACK_RATES) expect(validRate(r)).toBe(r)
    expect(validRate(3)).toBe(1)
    expect(validRate(Number.NaN)).toBe(1)
  })
})
