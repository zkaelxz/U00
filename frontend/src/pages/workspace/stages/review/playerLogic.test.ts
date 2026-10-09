import { describe, expect, it } from 'vitest'

import { clampTime, lineAt, overlayText, parseJumpTime, PLAYBACK_RATES, playerKeyAction, popoutCaptionPx, SEEK_STEP_FINE_S, SEEK_STEP_S, SUBTITLE_OPTIONS, subtitleSrc, validRate, type PlayerKey } from './playerLogic'

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

// A stand-in target that matches the way Element.closest does: by tag, or by role.
function target(...names: string[]): PlayerKey['target'] {
  return { closest: (selector) => (selector.split(',').some((s) => names.some((n) => s.trim() === n || s.includes(`[role="${n}"]`))) ? {} : null) }
}
const key = (k: string, extra: Partial<PlayerKey> = {}): PlayerKey => ({ key: k, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, target: target(), ...extra })

describe('playerKeyAction', () => {
  it('seeks by 5 s, or 1 s with Shift', () => {
    expect(playerKeyAction(key('ArrowRight'), 20, 100)).toEqual({ kind: 'seek', to: 20 + SEEK_STEP_S })
    expect(playerKeyAction(key('ArrowLeft'), 20, 100)).toEqual({ kind: 'seek', to: 20 - SEEK_STEP_S })
    expect(playerKeyAction(key('ArrowRight', { shiftKey: true }), 20, 100)).toEqual({ kind: 'seek', to: 20 + SEEK_STEP_FINE_S })
    expect(playerKeyAction(key('ArrowLeft', { shiftKey: true }), 20, 100)).toEqual({ kind: 'seek', to: 20 - SEEK_STEP_FINE_S })
  })
  it('clamps to the media', () => {
    expect(playerKeyAction(key('ArrowLeft'), 2, 100)).toEqual({ kind: 'seek', to: 0 })
    expect(playerKeyAction(key('ArrowRight'), 98, 100)).toEqual({ kind: 'seek', to: 100 })
    expect(playerKeyAction(key('ArrowRight'), 98, NaN)).toEqual({ kind: 'seek', to: 103 })
  })
  it('leaves the arrows to controls that use them', () => {
    for (const el of ['input', 'textarea', 'select', 'slider', 'textbox']) {
      expect(playerKeyAction(key('ArrowRight', { target: target(el) }), 20, 100)).toBeNull()
      expect(playerKeyAction(key('f', { target: target(el) }), 20, 100)).toBeNull()
    }
  })
  it('ignores combinations with Ctrl, Cmd or Alt', () => {
    for (const m of ['ctrlKey', 'metaKey', 'altKey'] as const) {
      expect(playerKeyAction(key('ArrowRight', { [m]: true }), 20, 100)).toBeNull()
      expect(playerKeyAction(key(' ', { [m]: true }), 20, 100)).toBeNull()
    }
  })
  it('toggles full screen with F and play with Space', () => {
    expect(playerKeyAction(key('f'), 0, 10)).toEqual({ kind: 'fullscreen' })
    expect(playerKeyAction(key('F'), 0, 10)).toEqual({ kind: 'fullscreen' })
    expect(playerKeyAction(key(' '), 0, 10)).toEqual({ kind: 'playpause' })
  })
  it('lets a focused button keep its own Space', () => {
    expect(playerKeyAction(key(' ', { target: target('button') }), 0, 10)).toBeNull()
    expect(playerKeyAction(key('ArrowRight', { target: target('button') }), 0, 10)).toEqual({ kind: 'seek', to: 5 })
  })
  it('does nothing for other keys', () => {
    expect(playerKeyAction(key('ArrowUp'), 0, 10)).toBeNull()
    expect(playerKeyAction(key('j'), 0, 10)).toBeNull()
    expect(playerKeyAction(key('F', { shiftKey: true }), 0, 10)).toBeNull()
  })
})

describe('overlayText', () => {
  it('keeps the lines and drops blanks and padding', () => {
    expect(overlayText('Hello\n  there  \n\n')).toBe('Hello\nthere')
    expect(overlayText('你好\nHello')).toBe('你好\nHello')
  })
  it('is empty for an empty cue, so no box is drawn', () => {
    expect(overlayText('')).toBe('')
    expect(overlayText(' \n \u00a0 ')).toBe('')
  })
})
