import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { ReviewLine } from '../../types/review'
import { START_SLACK_S } from '../reader/readAlongLogic'
import {
  checkStep, linesToCheck,
  attentionIndex, clockTenths, currentLine, fieldKey, fixIsDirty, focusAfterFix, FOLLOW_AT, FOLLOW_PAUSE_MS, followPaused, followScroll,
  lineHeading, lineToFix, loopLineAt, openFix, OWN_SCROLL_MS, ownScrollUntil, parkedOutcome, parkFix, positionKey, readParkedFix,
  quietSkip, QUIET_LEAD_S, resumePoint, rowMeta, saveFix, SPEAKER_COLOURS, speakerSlots, spokenOrder, stepIndex, titleKindAndLanguages, unparkedFix, unparkFix, verdictChange, watchKey,
  watchVerb, type LineFix,
} from './watchLogic'

const line = (id: number, start: number, end: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx: id - 1, start, end, zh: `zh ${id}`, en: `en ${id}`, speaker: null, speaker_manual: false, sfx: false, flag: null,
  flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, mark: null, unsure_words: null, ...over,
})

const lines = [line(1, 1, 2), line(2, 3, 4.5), line(3, 6, 7)]
const conflict = () => new ApiError(409, { code: 'conflict', message: 'changed' }, 'POST')

describe('loopLineAt', () => {
  it('is the line being spoken, as the card shows it', () => {
    expect(loopLineAt(lines, 3.2)?.id).toBe(2)
    expect(loopLineAt(lines, 2.5)?.id).toBe(1)
    expect(loopLineAt(lines, 0)).toBeNull()
  })
  it('never takes a line that ends at or before its start, which the sound would be put back from at every frame', () => {
    const odd = [line(1, 1, 2), line(2, 3, 3), line(3, 6, 5.5)]
    expect(loopLineAt(odd, 3.5)).toBeNull()
    expect(loopLineAt(odd, 6.2)).toBeNull()
    expect(currentLine(odd, 3.5)?.id).toBe(2)
    expect(loopLineAt(odd, 1.5)?.id).toBe(1)
  })
})

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
  const at = (...starts: number[]) => starts.map((s, i) => line(i + 1, s, s + 1))
  // Up or Down as the page does it: seek to the line's start, and the current line is whatever is under the playhead.
  const press = (ordered: ReviewLine[], from: number, step: 1 | -1) => {
    const to = stepIndex(ordered, from, step)
    return to === null ? null : ordered.indexOf(currentLine(ordered, ordered[to].start)!)
  }
  it('moves to the previous and next line', () => {
    expect(stepIndex(lines, 1, 1)).toBe(2)
    expect(stepIndex(lines, 1, -1)).toBe(0)
  })
  it('stays on the first and the last line', () => {
    expect(stepIndex(lines, 0, -1)).toBe(0)
    expect(stepIndex(lines, 2, 1)).toBe(2)
  })
  it('goes to the first line from before it, and nowhere without lines', () => {
    expect(stepIndex(lines, null, 1)).toBe(0)
    expect(stepIndex(lines, null, -1)).toBe(0)
    expect(stepIndex([], null, 1)).toBeNull()
  })
  it('steps back past a pair that starts together in the middle, instead of landing on the later one again', () => {
    const pair = at(0, 2, 4, 4 + START_SLACK_S / 2, 6)
    // The later of the two is the current line from the pair's start.
    expect(currentLine(pair, 4)?.id).toBe(4)
    expect(press(pair, 4, -1)).toBe(3)
    expect(press(pair, 3, -1)).toBe(1)
    expect(press(pair, 1, -1)).toBe(0)
    expect(press(pair, 1, 1)).toBe(3)
  })
  it('stays put on a pair that starts together at the very start', () => {
    const pair = at(0, 0, 2)
    expect(currentLine(pair, 0)?.id).toBe(2)
    expect(press(pair, 2, -1)).toBe(1)
    expect(stepIndex(pair, 1, -1)).toBe(1)
  })
  it('steps back past three lines that start together', () => {
    const three = at(0, 5, 5.01, 5.02, 8)
    expect(press(three, 4, -1)).toBe(3)
    expect(press(three, 3, -1)).toBe(0)
    expect(press(three, 0, 1)).toBe(3)
  })
  it('tells lines apart that start just more than the slack apart', () => {
    const near = at(0, 4, 4 + START_SLACK_S * 2)
    expect(press(near, 2, -1)).toBe(1)
  })
})

describe('lineToFix', () => {
  it('takes the line under the playhead at the moment of the press, not the one last drawn', () => {
    expect(lineToFix(lines, 6.2, lines[1])?.id).toBe(3)
    expect(lineToFix(lines, 0.5, lines[1])).toBeNull()
  })
  it('takes the drawn line when there is no playhead', () => {
    expect(lineToFix(lines, null, lines[1])?.id).toBe(2)
    expect(lineToFix(lines, null, null)).toBeNull()
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
    expect(lineHeading(l)).toBe('Line #24 · 1:02.5')
    expect(rowMeta(l)).toBe('#24 · 1:02.5')
  })
  it('says the kind and the languages from the shared labels, and nothing when it knows neither', () => {
    expect(titleKindAndLanguages('audio_drama', 'zh')).toBe('Audio drama · Chinese to English')
    expect(titleKindAndLanguages('video_drama', 'ko')).toBe('Video drama · Korean to English')
    expect(titleKindAndLanguages(null, 'ja')).toBe('Japanese to English')
    expect(titleKindAndLanguages('anime', null)).toBe('Anime')
    expect(titleKindAndLanguages(null, null)).toBe('')
    expect(titleKindAndLanguages('', '')).toBe('')
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
  const field = { closest: (s: string) => (s.includes('textarea') ? {} : null) }
  it('E fixes the line, Up and Down move between lines, L turns Loop line on or off', () => {
    expect(watchKey('e', body)).toBe('fix')
    expect(watchKey('arrowup', body)).toBe('prev')
    expect(watchKey('arrowdown', body)).toBe('next')
    expect(watchKey('l', body)).toBe('loop')
    expect(watchKey('j', body)).toBeNull()
    expect(watchKey(' ', body)).toBeNull()
  })
  it('leaves controls that use those keys themselves alone', () => {
    expect(watchKey('arrowdown', slider)).toBeNull()
    expect(watchKey('l', field)).toBeNull()
    expect(watchKey('e', field)).toBeNull()
  })
  // Matches the way Element.closest does, for an element that matches exactly these parts of a selector list.
  const matching = (...parts: string[]) => ({ closest: (s: string) => (s.split(', ').some((p) => parts.includes(p)) ? {} : null) })
  const range = matching('input')
  const ariaSlider = matching('[role="slider"]')
  it('E and L still work on the seek bar and on any slider, which use no letters; their arrow keys stay theirs', () => {
    for (const target of [range, ariaSlider]) {
      expect(watchKey('e', target)).toBe('fix')
      expect(watchKey('l', target)).toBe('loop')
      expect(watchKey('arrowup', target)).toBeNull()
      expect(watchKey('arrowdown', target)).toBeNull()
    }
  })
  it('text fields, selects, lists and editable regions keep every key, E and L included', () => {
    const text = matching('input', 'input:not([type="range"])')
    const editable = matching('[contenteditable]:not([contenteditable="false"])')
    for (const target of [text, matching('textarea'), matching('select'), editable, matching('[role="listbox"]'), matching('[role="combobox"]')]) {
      expect(watchKey('e', target)).toBeNull()
      expect(watchKey('l', target)).toBeNull()
      expect(watchKey('arrowdown', target)).toBeNull()
    }
  })
  it('Alt+Space plays or pauses from anywhere, the fix field and a slider included', () => {
    expect(watchKey('alt+ ', body)).toBe('play')
    expect(watchKey('alt+ ', field)).toBe('play')
    expect(watchKey('alt+ ', slider)).toBe('play')
    expect(watchKey('alt+l', body)).toBeNull()
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
    expect(r).toEqual({ kind: 'conflict', lines: fresh, fix: { lineId: 2, seen: 'Changed elsewhere', draft: 'Mine', conflict: true, now: 'Changed elsewhere', error: null } })
  })
  it('on a 409 whose reload fails keeps the draft and the old text, and has no new text to show', async () => {
    const reload = vi.fn(async () => { throw new ApiError(503, { code: 'unavailable', message: 'down' }, 'GET') })
    const r = await saveFix({ ...openFix(lines[1]), draft: 'Mine' }, { patch: vi.fn(async () => { throw conflict() }), reload })
    expect(r).toEqual({ kind: 'conflict', lines: null, fix: { lineId: 2, seen: 'en 2', draft: 'Mine', conflict: true, now: null, error: null } })
  })
  it('a second refusal after a failed reload shows the text the new reload found', async () => {
    const first = await saveFix({ ...openFix(lines[1]), draft: 'Mine' }, { patch: vi.fn(async () => { throw conflict() }), reload: vi.fn(async () => { throw new Error('offline') }) })
    if (first.kind !== 'conflict') throw new Error(first.kind)
    const fresh = [lines[0], line(2, 3, 4.5, { en: '' }), lines[2]]
    const r = await saveFix(first.fix, { patch: vi.fn(async () => { throw conflict() }), reload: vi.fn(async () => fresh) })
    expect(r).toMatchObject({ kind: 'conflict', fix: { seen: '', draft: 'Mine', now: '' } })
  })
  it('keeps the draft and the error on any other failure', async () => {
    const boom = new ApiError(500, { code: 'internal_error', message: 'boom' }, 'POST')
    const r = await saveFix({ ...openFix(lines[1]), draft: 'Mine' }, { patch: vi.fn(async () => { throw boom }), reload: vi.fn() })
    expect(r).toEqual({ kind: 'failed', fix: { lineId: 2, seen: 'en 2', draft: 'Mine', conflict: false, now: null, error: boom } })
  })
})

describe('the fix kept from being lost', () => {
  const store = () => {
    const m = new Map<string, string>()
    return { m, getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) }
  }
  it('is dirty only when Save would send something', () => {
    expect(fixIsDirty(null)).toBe(false)
    expect(fixIsDirty(openFix(lines[1]))).toBe(false)
    expect(fixIsDirty({ ...openFix(lines[1]), draft: 'Mine' })).toBe(true)
    expect(fixIsDirty({ ...openFix(lines[1]), draft: '' })).toBe(true)
  })
  it('parks the line, the English seen and the draft per title, and reads them back', () => {
    const s = store()
    // A whole open fix is passed; only what is needed to open it again is kept.
    const fix: LineFix = { ...openFix(lines[1]), draft: 'Mine', now: 'x', error: new Error('e') }
    parkFix(3, fix, s)
    expect([...s.m.keys()]).toEqual(['baihe.watch.draft.3'])
    expect(readParkedFix(3, s)).toEqual({ lineId: 2, seen: 'en 2', draft: 'Mine', refused: false })
    expect(readParkedFix(4, s)).toBeNull()
    unparkFix(3, s)
    expect(readParkedFix(3, s)).toBeNull()
  })
  it('marks a fix whose last Save was refused, and reads a record without the mark as never refused', () => {
    const s = store()
    const refused: LineFix = { ...openFix(lines[1]), seen: 'Theirs', draft: 'Mine', conflict: true, now: 'Theirs' }
    parkFix(3, refused, s)
    expect(JSON.parse(s.m.get('baihe.watch.draft.3')!)).toEqual({ lineId: 2, seen: 'Theirs', draft: 'Mine', refused: true })
    expect(readParkedFix(3, s)).toEqual({ lineId: 2, seen: 'Theirs', draft: 'Mine', refused: true })
    for (const mark of ['', ', "refused": "yes"']) {
      s.setItem('baihe.watch.draft.3', `{"lineId": 2, "seen": "en 2", "draft": "Mine"${mark}}`)
      expect(readParkedFix(3, s)).toEqual({ lineId: 2, seen: 'en 2', draft: 'Mine', refused: false })
    }
  })
  it('drops anything under its key that is not a parked fix', () => {
    for (const junk of ['not json', 'null', '{"lineId":"2","seen":"a","draft":"b"}', '{"lineId":2,"seen":"a"}']) {
      const s = store()
      s.setItem('baihe.watch.draft.3', junk)
      expect(readParkedFix(3, s)).toBeNull()
      expect(s.m.size).toBe(0)
    }
  })
  it('does nothing without a store, or with one that throws', () => {
    const throwing = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('full') }, removeItem: () => { throw new Error('blocked') } }
    expect(() => parkFix(3, openFix(lines[1]), null)).not.toThrow()
    expect(() => parkFix(3, openFix(lines[1]), throwing)).not.toThrow()
    expect(readParkedFix(3, null)).toBeNull()
    expect(readParkedFix(3, throwing)).toBeNull()
  })
  it('on the next visit opens again, unless its line is gone or already says the fix', () => {
    const parked = { lineId: 2, seen: 'en 2', draft: 'Mine' }
    expect(parkedOutcome(parked, undefined)).toBe('gone')
    expect(parkedOutcome(parked, { en: 'en 2' })).toBe('open')
    // Changed by someone else meanwhile: it opens, and Save is then refused with what the line says now.
    expect(parkedOutcome(parked, { en: 'Theirs' })).toBe('open')
    // The save sent on leaving went through and only its answer was lost.
    expect(parkedOutcome(parked, { en: 'Mine' })).toBe('saved')
  })
  it('opens again measured against the English seen first, so a line changed since is refused on Save', () => {
    expect(unparkedFix({ lineId: 2, seen: 'en 2', draft: 'Mine', refused: false }, { en: 'Theirs' })).toEqual({ lineId: 2, seen: 'en 2', draft: 'Mine', conflict: false, now: null, error: null })
  })
  it('a refused one opens refused again, against what the line says now, so Save sends it over that', () => {
    const parked = { lineId: 2, seen: 'Theirs', draft: 'Mine', refused: true }
    expect(unparkedFix(parked, { en: 'Changed again' })).toEqual({ lineId: 2, seen: 'Changed again', draft: 'Mine', conflict: true, now: 'Changed again', error: null })
    expect(unparkedFix(parked, { en: '' })).toMatchObject({ seen: '', conflict: true, now: '' })
    // Its line gone or already saying the draft, it is not opened at all.
    expect(parkedOutcome(parked, undefined)).toBe('gone')
    expect(parkedOutcome(parked, { en: 'Mine' })).toBe('saved')
    expect(parkedOutcome(parked, { en: 'Changed again' })).toBe('open')
  })
})

describe('focusAfterFix', () => {
  it('goes to the card after Save and play, so Space pauses instead of opening the fix again', () => {
    expect(focusAfterFix('saved')).toBe('card')
    expect(focusAfterFix('unchanged')).toBe('card')
  })
  it('goes back into the box after a refused save, and to Fix this line after Cancel', () => {
    expect(focusAfterFix('conflict')).toBe('field')
    expect(focusAfterFix('failed')).toBe('field')
    expect(focusAfterFix('cancel')).toBe('fix-button')
  })
})

describe('following the sound', () => {
  const view = { height: 500, scrollTop: 1000, maxScroll: 3000 }
  it('puts the spoken row\'s top part way down the list, so the coming lines show under it', () => {
    expect(FOLLOW_AT).toBe(0.3)
    // A row at the list's bottom edge comes up to 150px down.
    expect(followScroll(480, view)).toBe(330)
    expect(followScroll(-200, view)).toBe(-350)
    expect(followScroll(150, view)).toBe(0)
  })
  it('stops at the list\'s ends', () => {
    expect(followScroll(100, { ...view, scrollTop: 0 })).toBe(0)
    expect(followScroll(-900, view)).toBe(-1000)
    expect(followScroll(2000, { ...view, scrollTop: 2900 })).toBe(100)
    expect(followScroll(300, { height: 500, scrollTop: 0, maxScroll: 0 })).toBe(0)
  })
  it('waits five seconds after the person last scrolled the list, and not without that', () => {
    expect(FOLLOW_PAUSE_MS).toBe(5000)
    expect(followPaused(null, 10_000)).toBe(false)
    expect(followPaused(10_000, 10_000)).toBe(true)
    expect(followPaused(10_000, 14_999)).toBe(true)
    expect(followPaused(10_000, 15_000)).toBe(false)
  })
  it("counts the page's own scroll as its own until its scrollend, or for a second where that event is not fired", () => {
    expect(ownScrollUntil(10_000, true)).toBe(Infinity)
    expect(OWN_SCROLL_MS).toBe(1000)
    expect(ownScrollUntil(10_000, false)).toBe(11_000)
  })
})

describe('the jump to a marked or flagged line', () => {
  const spots = [line(1, 1, 2), line(2, 3, 4, { mark: 'wrong' }), line(3, 5, 6), line(4, 7, 8, { flag: 'long' }), line(5, 9, 10)]

  it('finds the nearest marked or flagged line either way, and none past the ends', () => {
    expect(attentionIndex(spots, 0, 1)).toBe(1)
    expect(attentionIndex(spots, 1, 1)).toBe(3)
    expect(attentionIndex(spots, 3, -1)).toBe(1)
    expect(attentionIndex(spots, 3, 1)).toBeNull()
    expect(attentionIndex(spots, 1, -1)).toBeNull()
  })

  it('starts from the first line going on and the last going back when no line is current', () => {
    expect(attentionIndex(spots, null, 1)).toBe(1)
    expect(attentionIndex(spots, null, -1)).toBe(3)
  })

  it('finds nothing when no line is marked or flagged', () => {
    expect(attentionIndex([line(1, 1, 2), line(2, 3, 4)], null, 1)).toBeNull()
  })
})

describe('what a verdict does to a line', () => {
  it('checks an unchecked line and takes the check off a checked one', () => {
    expect(verdictChange(line(1, 1, 2), 'good')).toEqual({ kind: 'reviewed', to: true })
    expect(verdictChange(line(1, 1, 2, { reviewed: true }), 'good')).toEqual({ kind: 'reviewed', to: false })
  })

  it('sets Wrong or Mixed, and clears it when pressed again', () => {
    expect(verdictChange(line(1, 1, 2), 'wrong')).toEqual({ kind: 'mark', to: 'wrong' })
    expect(verdictChange(line(1, 1, 2, { mark: 'wrong' }), 'wrong')).toEqual({ kind: 'mark', to: null })
    expect(verdictChange(line(1, 1, 2, { mark: 'wrong' }), 'mixed')).toEqual({ kind: 'mark', to: 'mixed' })
  })
})

describe('the mark and jump keys', () => {
  const body = { closest: () => null }
  const field = { closest: () => ({}) }

  it('maps G, X, M and N, P to the verdicts and the jump', () => {
    expect(['g', 'x', 'm', 'n', 'p'].map((k) => watchKey(k, body))).toEqual(['good', 'wrong', 'mixed', 'nextmarked', 'prevmarked'])
  })

  it('leaves the letters to a field being typed in', () => {
    expect(['g', 'x', 'm', 'n', 'p'].map((k) => watchKey(k, field))).toEqual([null, null, null, null, null])
  })
})

describe('lines to check', () => {
  const l = (id: number, reviewed = false) => ({ id, reviewed })
  const ordered = [l(1), l(2, true), l(3), l(4), l(5)]
  const index = new Map(ordered.map((x, i) => [x.id, i]))
  it('keeps the risky lines nobody has said look right, in spoken order', () => {
    expect(linesToCheck(ordered, new Set([4, 2, 1])).map((x) => x.id)).toEqual([1, 4])
  })
  it('steps to the next and previous one from the spoken line', () => {
    const checks = linesToCheck(ordered, new Set([1, 4]))
    expect(checkStep(checks, index, null, 1)).toBe(0)
    expect(checkStep(checks, index, 0, 1)).toBe(1)
    expect(checkStep(checks, index, 3, 1)).toBeNull()
    expect(checkStep(checks, index, 3, -1)).toBe(0)
    expect(checkStep(checks, index, 0, -1)).toBeNull()
  })
})

describe('speakerSlots', () => {
  it('numbers speakers by first appearance and skips lines with none', () => {
    const slots = speakerSlots([line(1, 1, 2, { speaker: 'Chime' }), line(2, 3, 4), line(3, 5, 6, { speaker: 'Anvil' }), line(4, 7, 8, { speaker: 'Chime' })])
    expect([...slots]).toEqual([['Chime', 0], ['Anvil', 1]])
  })

  it('wraps after the colours the page defines, so a crowded stream repeats rather than fails', () => {
    const many = Array.from({ length: SPEAKER_COLOURS + 1 }, (_, i) => line(i + 1, i, i + 1, { speaker: `S${i}` }))
    expect(speakerSlots(many).get(`S${SPEAKER_COLOURS}`)).toBe(0)
  })
})

describe('quietSkip', () => {
  const spoken = [line(1, 10, 12), line(2, 14, 15), line(3, 60, 62)]

  it('jumps a long quiet stretch to just before the next line', () => {
    expect(quietSkip(spoken, 20)).toBe(60 - QUIET_LEAD_S)
  })

  it('offers the intro before the first line', () => {
    expect(quietSkip(spoken, 0)).toBe(10 - QUIET_LEAD_S)
  })

  it('stays out of a line being spoken and of short pauses', () => {
    expect(quietSkip(spoken, 11)).toBeNull()
    expect(quietSkip(spoken, 12.5)).toBeNull()
  })

  it('has nothing to skip to after the last line', () => {
    expect(quietSkip(spoken, 70)).toBeNull()
  })
})
