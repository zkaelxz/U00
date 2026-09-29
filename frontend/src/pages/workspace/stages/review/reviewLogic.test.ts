import { describe, expect, it } from 'vitest'

import { ApiError } from '../../../../api/client'
import type { ReviewLine } from '../../../../types/review'
import {
  adjacentRun,
  buildPatch,
  chipLabel,
  codePointOffset,
  draftFromLine,
  emptyMessage,
  estimateSplitTime,
  formatDuration,
  gapForNewLine,
  initialActiveId,
  isDirty,
  jobRunsOnDrama,
  JOB_RUNNING_MESSAGE,
  keptNote,
  lineRange,
  LINES_CHANGED_MESSAGE,
  mergedText,
  nextFlaggedId,
  pageForPosition,
  pageStillMatches,
  resegmentSummary,
  splitPieces,
  stepFrom,
  structureErrorText,
} from './reviewLogic'

const mk = (id: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx: id, start: id, end: id + 1, zh: `句${id}`, en: `Line ${id}`, speaker: null,
  speaker_manual: false, sfx: false, flag: null, flag_note: null, dub_filename: null, ...over,
})

describe('drafts', () => {
  it('sends sfx only when it changed', () => {
    const line = mk(1)
    expect(buildPatch(line, { ...draftFromLine(line), sfx: true })).toEqual({ sfx: true, expected: { sfx: false } })
  })
  it('is dirty when something would be sent or the draft is invalid', () => {
    const line = mk(1)
    expect(isDirty(line, draftFromLine(line))).toBe(false)
    expect(isDirty(line, { ...draftFromLine(line), en: 'x' })).toBe(true)
    expect(isDirty(line, { ...draftFromLine(line), end: '' })).toBe(true)
  })
})

describe('active line', () => {
  it('starts on the first flagged, else first untranslated, else line 1', () => {
    expect(initialActiveId([mk(1), mk(2, { en: '' }), mk(3, { flag: 'x' })])).toBe(3)
    expect(initialActiveId([mk(1), mk(2, { en: '' })])).toBe(2)
    expect(initialActiveId([mk(1), mk(2)])).toBe(1)
    expect(initialActiveId([])).toBeNull()
  })
  it('steps within a page and asks for a page change at the edges', () => {
    const lines = [mk(1), mk(2)]
    const both = { next: true, prev: true }
    expect(stepFrom(lines, 1, 1, both)).toEqual({ id: 2 })
    expect(stepFrom(lines, 2, 1, both)).toEqual({ page: 'next' })
    expect(stepFrom(lines, 1, -1, both)).toEqual({ page: 'prev' })
    expect(stepFrom(lines, 2, 1, { next: false, prev: true })).toBeNull()
    expect(stepFrom(lines, 99, -1, both)).toEqual({ id: 2 })
  })
  it('finds the next and previous flagged line', () => {
    const lines = [mk(1, { flag: 'a' }), mk(2), mk(3, { flag: 'b' })]
    expect(nextFlaggedId(lines, -1, 1)).toBe(1)
    expect(nextFlaggedId(lines, -1, -1)).toBe(3)
    expect(nextFlaggedId(lines, 0, 1)).toBe(3)
    expect(nextFlaggedId(lines, 2, 1)).toBeNull()
    expect(nextFlaggedId(lines, 2, -1)).toBe(1)
  })
  it('maps a position to its page', () => {
    expect(pageForPosition(0)).toBe(1)
    expect(pageForPosition(39)).toBe(1)
    expect(pageForPosition(40)).toBe(2)
  })
})

describe('split', () => {
  it('counts code points, not UTF-16 units', () => {
    const text = '😀你好'
    expect(codePointOffset(text, 2)).toBe(1)
    expect(codePointOffset(text, 3)).toBe(2)
    expect(splitPieces(text, 1)).toEqual(['😀', '你好'])
  })
  it('estimates the cut time in proportion to the text', () => {
    expect(estimateSplitTime({ start: 10, end: 14, zh: '一二三四' }, 1)).toBe(11)
    expect(estimateSplitTime({ start: 0, end: 1, zh: '' }, 0)).toBe(0.5)
  })
})

describe('add and merge', () => {
  it('fills the gap after a line', () => {
    expect(gapForNewLine({ end: 5 }, { start: 6 })).toEqual({ start: 5, end: 6 })
    expect(gapForNewLine({ end: 5 }, { start: 9 })).toEqual({ start: 5, end: 7 })
    expect(gapForNewLine({ end: 5 }, { start: 5.1 })).toEqual({ start: 5, end: 7 })
    expect(gapForNewLine(null, null)).toEqual({ start: 0, end: 2 })
  })
  it('picks adjacent ids and previews the joined text', () => {
    expect(adjacentRun([1, 2, 3, 4], 2, 2)).toEqual([2, 3])
    expect(adjacentRun([1, 2, 3], 3, 2)).toBeNull()
    expect(adjacentRun([1, 2, 3], 9, 2)).toBeNull()
    expect(mergedText([{ zh: '你 ', en: 'Hi ' }, { zh: ' 好', en: ' there' }])).toEqual({ zh: '你好', en: 'Hi there' })
    expect(lineRange([{ idx: 4 }, { idx: 6 }])).toBe('#5–#7')
    expect(lineRange([{ idx: 4 }])).toBe('#5')
  })
})

describe('structure guards', () => {
  it('checks the loaded page against the current id list', () => {
    expect(pageStillMatches([1, 2, 3, 4], [3, 4], 'all', 2, 2)).toBe(true)
    expect(pageStillMatches([1, 9, 3, 4], [3, 4], 'all', 1, 2)).toBe(false)
    expect(pageStillMatches([1, 2, 3], [1, 3], 'flagged', 1)).toBe(true)
    expect(pageStillMatches([3, 2, 1], [1, 3], 'search', 1)).toBe(false)
    expect(pageStillMatches([1, 2], [1, 7], 'flagged', 1)).toBe(false)
  })
  it('turns a 409 into plain text', () => {
    expect(structureErrorText(new ApiError(409, { code: 'conflict', message: 'A background job is still running' }))).toBe(JOB_RUNNING_MESSAGE)
    expect(structureErrorText(new ApiError(409, { code: 'conflict', message: "This drama's lines changed" }))).toBe(LINES_CHANGED_MESSAGE)
    expect(structureErrorText(new ApiError(422, { code: 'invalid_input', message: 'x' }))).toBeNull()
  })
  it('matches running jobs to the drama by id suffix', () => {
    expect(jobRunsOnDrama([{ job_id: 'translate_3', status: 'running' }], 3)).toBe(true)
    expect(jobRunsOnDrama([{ job_id: 'translate_13', status: 'running' }], 3)).toBe(false)
    expect(jobRunsOnDrama([{ job_id: 'translate_3', status: 'done' }], 3)).toBe(false)
    expect(jobRunsOnDrama([{ job_id: 'bulk_translate_3', status: 'queued' }], 3)).toBe(true)
  })
})

describe('labels', () => {
  it('writes chips, empty states and durations', () => {
    expect(chipLabel('flagged', 4, true)).toBe('⚑ 4')
    expect(chipLabel('untranslated', 14, false)).toBe('Untranslated 14')
    expect(chipLabel('all', null, false)).toBe('All')
    expect(emptyMessage('flagged', '')).toBe('No flagged lines. Nice.')
    expect(emptyMessage('all', 'abc')).toBe('No lines match “abc”.')
    expect(formatDuration(1450)).toBe('24:10')
    expect(formatDuration(3723)).toBe('1:02:03')
    expect(formatDuration(NaN)).toBe('–')
  })
})

describe('re-segmentation summary', () => {
  it('reads as one line', () => {
    const p = { drama_id: 1, source_line_ids: [], line_count_before: 40, line_count_after: 46, changed: new Array(6).fill({ line_id: 1, idx: 1, zh: '', pieces: [] }), translated: 3, flagged: 1, notes: 0, needs_confirm: true }
    expect(resegmentSummary(p)).toBe('40 → 46 lines; 6 change; 3 translated, 1 flagged, 0 notes would be split')
  })
})

describe('keptNote', () => {
  it('says nothing without conflicts and counts them otherwise', () => {
    expect(keptNote(0)).toBe('')
    expect(keptNote(1)).toBe(' 1 line was edited meanwhile and kept.')
    expect(keptNote(3)).toBe(' 3 lines were edited meanwhile and kept.')
  })
})
