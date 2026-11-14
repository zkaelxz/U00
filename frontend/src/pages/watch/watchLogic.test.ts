import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { ReviewLine } from '../../types/review'
import {
  clockTenths, currentLine, fieldKey, kindBadge, lineHeading, nearestScroll, openFix, positionKey, resumePoint, rowMeta,
  saveFix, spokenOrder, stepIndex, watchKey, watchVerb,
} from './watchLogic'

const line = (id: number, start: number, end: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx: id - 1, start, end, zh: `zh ${id}`, en: `en ${id}`, speaker: null, speaker_manual: false, sfx: false, flag: null,
  flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, ...over,
})

const lines = [line(1, 1, 2), line(2, 3, 4.5), line(3, 6, 7)]
const conflict = () => new ApiError(409, { code: 'conflict', message: 'changed' }, 'POST')

describe('currentLine', () => {
  it('is nothing before the first line starts', () => {
    expect(currentLine(lines, 0)).toBeNull()
    expect(currentLine(lines, 0.9)).toBeNull()
    expect(currentLine([], 5)).toBeNull()
  })
  it('is the line being spoken', () => {
    expect(currentLine(lines, 1)?.id).toBe(1)
    expect(currentLine(lines, 3.2)?.id).toBe(2)
  })
  it('stays on a line through the pause after it, so the card never goes blank between two lines', () => {
    expect(currentLine(lines, 2.5)?.id).toBe(1)
    expect(currentLine(lines, 5.9)?.id).toBe(2)
  })
  it('stays on the last line after it ends', () => {
    expect(currentLine(lines, 7.5)?.id).toBe(3)
    expect(currentLine(lines, 600)?.id).toBe(3)
  })
  it('follows the spoken order, not the list order', () => {
    const ordered = spokenOrder([line(2, 3, 4), line(1, 1, 2)])
    expect(ordered.map((l) => l.id)).toEqual([1, 2])
    expect(currentLine(ordered, 1.5)?.id).toBe(1)
  })
})

describe('resumePoint', () => {
  it('starts at the beginning under 5 seconds in', () => {
    expect(resumePoint(0, 300)).toBeNull()
    expect(resumePoint(4.9, 300)).toBeNull()
  })
  it('starts at the beginning within 10 seconds of the end', () => {
    expect(resumePoint(290.1, 300)).toBeNull()
    expect(resumePoint(300, 300)).toBeNull()
  })
  it('goes back to the saved place in between', () => {
    expect(resumePoint(5, 300)).toBe(5)
    expect(resumePoint(247, 300)).toBe(247)
    expect(resumePoint(290, 300)).toBe(290)
  })
  it('never seeks without a known length', () => {
    expect(resumePoint(60, NaN)).toBeNull()
    expect(resumePoint(NaN, 300)).toBeNull()
  })
  it('is kept per title', () => {
    expect(positionKey(3)).toBe('watch.at.3')
  })
})

describe('stepIndex', () => {
  it('moves to the previous and next line', () => {
    expect(stepIndex(1, 1, 3)).toBe(2)
    expect(stepIndex(1, -1, 3)).toBe(0)
  })
  it('stays on the first and the last line', () => {
    expect(stepIndex(0, -1, 3)).toBe(0)
    expect(stepIndex(2, 1, 3)).toBe(2)
  })
  it('goes to the first line from before it, and nowhere without lines', () => {
    expect(stepIndex(null, 1, 3)).toBe(0)
    expect(stepIndex(null, -1, 3)).toBe(0)
    expect(stepIndex(null, 1, 0)).toBeNull()
  })
})

describe('labels', () => {
  it('times a line to the tenth of a second', () => {
    expect(clockTenths(62.5)).toBe('1:02.5')
    expect(clockTenths(0)).toBe('0:00.0')
    expect(clockTenths(59.99)).toBe('0:59.9')
    expect(clockTenths(3725.25)).toBe('1:02:05.2')
  })
  it('names the line by its 1-based number', () => {
    const l = line(24, 62.5, 64)
    expect(lineHeading(l)).toBe('Line 24 · 1:02.5')
    expect(rowMeta(l)).toBe('24 · 1:02.5')
  })
  it('says the kind and the languages from the shared labels', () => {
    expect(kindBadge('audio_drama', 'zh')).toBe('Audio drama · Chinese to English')
    expect(kindBadge('video_drama', 'ko')).toBe('Video drama · Korean to English')
    expect(kindBadge(null, 'ja')).toBe('Japanese to English')
    expect(kindBadge('anime', null)).toBe('Anime')
  })
  it('calls the page Watch for video and Listen for audio', () => {
    expect(watchVerb('video', 'audio')).toBe('Watch')
    expect(watchVerb('audio', 'video')).toBe('Listen')
    expect(watchVerb('subtitle', 'video')).toBe('Watch')
    expect(watchVerb('subtitle', 'audio')).toBe('Listen')
  })
})

describe('keys', () => {
  const body = { closest: () => null }
  const slider = { closest: (s: string) => (s.includes('[role="slider"]') ? {} : null) }
  it('E fixes the line, Up and Down move between lines', () => {
    expect(watchKey('e', body)).toBe('fix')
    expect(watchKey('arrowup', body)).toBe('prev')
    expect(watchKey('arrowdown', body)).toBe('next')
    expect(watchKey('j', body)).toBeNull()
    expect(watchKey(' ', body)).toBeNull()
  })
  it('leaves controls that use those keys themselves alone', () => {
    expect(watchKey('arrowdown', slider)).toBeNull()
  })
  it('saves on Ctrl+Enter or Cmd+Enter and cancels on Esc inside the field', () => {
    const k = { ctrlKey: false, metaKey: false, altKey: false }
    expect(fieldKey({ ...k, key: 'Enter', ctrlKey: true })).toBe('save')
    expect(fieldKey({ ...k, key: 'Enter', metaKey: true })).toBe('save')
    expect(fieldKey({ ...k, key: 'Enter' })).toBeNull()
    expect(fieldKey({ ...k, key: 'Escape' })).toBe('cancel')
    expect(fieldKey({ ...k, key: 'Escape', isComposing: true })).toBeNull()
  })
})

describe('saveFix', () => {
  it('sends the new English with the English that was seen', async () => {
    const saved = line(2, 3, 4.5, { en: 'Fixed' })
    const patch = vi.fn(async () => saved)
    const reload = vi.fn(async () => lines)
    const r = await saveFix({ ...openFix(lines[1]), draft: 'Fixed' }, { patch, reload })
    expect(patch).toHaveBeenCalledWith(2, { en: 'Fixed', expected: { en: 'en 2' } })
    expect(r).toEqual({ kind: 'saved', line: saved })
    expect(reload).not.toHaveBeenCalled()
  })
  it('sends nothing when the text did not change', async () => {
    const patch = vi.fn()
    const r = await saveFix(openFix(lines[1]), { patch, reload: vi.fn() })
    expect(r).toEqual({ kind: 'unchanged' })
    expect(patch).not.toHaveBeenCalled()
  })
  it('on a 409 reads the lines again and keeps the draft, measured against the new text', async () => {
    const fresh = [lines[0], line(2, 3, 4.5, { en: 'Changed elsewhere' }), lines[2]]
    const reload = vi.fn(async () => fresh)
    const r = await saveFix({ ...openFix(lines[1]), draft: 'Mine' }, { patch: vi.fn(async () => { throw conflict() }), reload })
    expect(reload).toHaveBeenCalledOnce()
    expect(r).toEqual({ kind: 'conflict', lines: fresh, fix: { lineId: 2, seen: 'Changed elsewhere', draft: 'Mine', conflict: true, error: null } })
  })
  it('keeps the draft and the error on any other failure', async () => {
    const boom = new ApiError(500, { code: 'internal_error', message: 'boom' }, 'POST')
    const r = await saveFix({ ...openFix(lines[1]), draft: 'Mine' }, { patch: vi.fn(async () => { throw boom }), reload: vi.fn() })
    expect(r).toEqual({ kind: 'failed', fix: { lineId: 2, seen: 'en 2', draft: 'Mine', conflict: false, error: boom } })
  })
})

describe('nearestScroll', () => {
  const box = { top: 100, bottom: 300 }
  it('does not move a row that is in view', () => {
    expect(nearestScroll({ top: 120, bottom: 160 }, box)).toBe(0)
  })
  it('moves the list as little as it takes', () => {
    expect(nearestScroll({ top: 80, bottom: 120 }, box)).toBe(-20)
    expect(nearestScroll({ top: 290, bottom: 330 }, box)).toBe(30)
  })
  it('lines up a row taller than the list with its top', () => {
    expect(nearestScroll({ top: 150, bottom: 400 }, box)).toBe(50)
  })
})
