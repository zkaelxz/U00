import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ApiError } from '../../../../api/client'
import type { ReviewLine } from '../../../../types/review'
import { TRANSLATION_ONLY } from '../../translateForm'
import { buildPatch, timingPatch, draftFromLine, isDirty, languageSetText, LINE_LANGUAGES, lineLangChip, titleDefaultLabel } from './reviewDraft'
import { adjacentRun, speakerTimeFooter, speakerTimeLines, chipLabel, codePointOffset, emptyMessage, cutBoundaries, estimateSplitTime, proportionalCut, snapCut, stepToBoundary, formatDuration, gapForNewLine, initialActiveId, jobRunsOnDrama, keptNote, lineRange, LINES_CHANGED_MESSAGE, mergedText, flaggedStep, nextFlaggedId, pageForPosition, pageStillMatches, splitPieces, splitTranslationPieces, stepFrom, structureErrorText, undoDoneMessage, undoHandleOf, undoRefusal, restoreLossText, UNDO_CHANGED_MESSAGE, UNDO_GONE_MESSAGE, UNDO_NOTES_MESSAGE } from './reviewLogic'
import { resplitNeedsConfirm, resplitPreviewSummary, resplitSummary, canResegmentWith, droppedText, llmApplyProblem, llmApplyProblemText, llmPreviewSummary, resegmentCostNote, resegmentEngines, JOB_RUNNING_MESSAGE, resegmentSummary } from './reviewResegment'

const mk = (id: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx: id, start: id, end: id + 1, zh: `句${id}`, en: `Line ${id}`, speaker: null,
  speaker_manual: false, sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, ...over,
})

describe('spoken language', () => {
  it('shows a chip only for a line in another language than the title', () => {
    expect(lineLangChip(null, 'ja')).toBeNull()
    expect(lineLangChip('ja', 'ja')).toBeNull()
    expect(lineLangChip('ko', 'ja')).toBe('KO')
    expect(lineLangChip('en', 'zh')).toBe('EN')
    // A drama without a stored source language is Chinese, as on the server.
    expect(lineLangChip('zh', null)).toBeNull()
  })
  it('offers the server-side list and names the title default', () => {
    expect([...LINE_LANGUAGES]).toEqual(['zh', 'ja', 'ko', 'en'])
    expect(titleDefaultLabel('ja')).toBe('Title default (Japanese)')
    expect(titleDefaultLabel(null)).toBe('Title default (Chinese)')
  })
  it('drafts and patches lang, with "" as the title default', () => {
    const line = mk(1, { lang: 'ko' })
    expect(draftFromLine(line).lang).toBe('ko')
    expect(draftFromLine(mk(2)).lang).toBe('')
    expect(buildPatch(line, draftFromLine(line))).toBeNull()
    expect(buildPatch(line, { ...draftFromLine(line), lang: '' })).toEqual({ lang: '', expected: { lang: 'ko' } })
    expect(buildPatch(mk(2), { ...draftFromLine(mk(2)), lang: 'en' })).toEqual({ lang: 'en', expected: { lang: '' } })
  })
  it('describes a bulk set in sentence case', () => {
    expect(languageSetText(1, 'ko', 'ja')).toBe('Set 1 line to Korean.')
    expect(languageSetText(3, '', 'ja')).toBe('Set 3 lines to the title default (Japanese).')
    expect(languageSetText(0, 'en', 'ja')).toBe('Nothing changed: already English.')
  })
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
  it('asks the server from the focused row in a filtered view', () => {
    // untranslated view: lines 4 and 9 shown; a hidden flagged line may sit between
    const lines = [mk(4), mk(9, { flag: 'uncertain' }), mk(12)]
    expect(flaggedStep(lines, 'untranslated', 0, 1)).toEqual({ fromId: 4 })
    expect(flaggedStep(lines, 'untranslated', 2, -1)).toEqual({ fromId: 12 })
    // nothing focused: the page, then its edge
    expect(flaggedStep(lines, 'untranslated', -1, 1)).toEqual({ id: 9 })
    // "all" pages hold every line, so the page is searched first
    expect(flaggedStep(lines, 'all', 0, 1)).toEqual({ id: 9 })
    expect(flaggedStep(lines, 'all', 1, 1)).toEqual({ fromId: 12 })
    expect(flaggedStep(lines, 'all', 1, -1)).toEqual({ fromId: 4 })
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
  it('trusts the server drama_id and ignores stale rows', () => {
    expect(jobRunsOnDrama([{ job_id: 'sourceimport_25', status: 'running', drama_id: null }], 25)).toBe(false)
    expect(jobRunsOnDrama([{ job_id: 'transcribe_25', status: 'running', drama_id: 25 }], 25)).toBe(true)
    expect(jobRunsOnDrama([{ job_id: 'transcribe_25', status: 'running', drama_id: 25, stale: true }], 25)).toBe(false)
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

describe('AI re-segmentation preview (R47)', () => {
  // No translation-only engine is offered now; the filter still applies to one.
  beforeEach(() => TRANSLATION_ONLY.push('fake_mt'))
  afterEach(() => {
    TRANSLATION_ONLY.length = 0
  })
  const eng = (name: string, free = false) => ({ name, label: name.toUpperCase(), free, models: null, key_configured: true })
  const config = {
    engines: [eng('claude'), eng('ollama', true), eng('fake_mt'), eng('gemini')],
    month_spend: 1.5,
    monthly_cap_usd: 10,
    cap_applies_by_engine: { claude: true, ollama: false, fake_mt: true, gemini: false },
  }

  it('leaves translation-only engines out of the picker', () => {
    expect(resegmentEngines(config.engines).map((e) => e.name)).toEqual(['claude', 'ollama', 'gemini'])
    expect(canResegmentWith('fake_mt')).toBe(false)
    expect(canResegmentWith('')).toBe(true)
  })

  it('says what the preview costs before it starts', () => {
    expect(resegmentCostNote(config, 'claude')).toBe('A paid AI call, counted toward the monthly spending cap. Spent this month: $1.50 of $10.00.')
    expect(resegmentCostNote(config, 'ollama')).toBe('OLLAMA is free to run.')
    expect(resegmentCostNote(config, 'gemini')).toBe('An AI call; this engine is not counted toward the monthly cap. Spent this month: $1.50 of $10.00.')
    expect(resegmentCostNote({ ...config, monthly_cap_usd: 0 }, 'claude')).toContain('(no monthly cap)')
    expect(resegmentCostNote(null, '')).toBe('A paid AI call, counted toward the monthly spending cap.')
  })

  it('lists only what would be dropped', () => {
    expect(droppedText({ translated: 2, flagged: 1, notes: 1 })).toBe('2 translations, 1 flag and 1 note on the lines being split will be dropped.')
    expect(droppedText({ translated: 1, flagged: 0, notes: 3 })).toBe('1 translation and 3 notes on the lines being split will be dropped.')
    expect(droppedText({ translated: 0, flagged: 2, notes: 0 })).toBe('2 flags on the lines being split will be dropped.')
    expect(droppedText({ translated: 0, flagged: 0, notes: 0 })).toMatch(/any line that is split loses them/)
  })

  it('summarises counts and the engine in plain words', () => {
    const p = { drama_id: 1, source_line_ids: [1, 2], line_count_before: 2, line_count_after: 4, changed: new Array(2).fill({ line_id: 1, idx: 0, zh: '', pieces: [] }), translated: 0, flagged: 0, notes: 0, needs_confirm: false, engine: 'claude' }
    expect(llmPreviewSummary(p)).toBe('2 → 4 lines · 2 lines split · by Claude')
    expect(llmPreviewSummary({ ...p, changed: p.changed.slice(0, 1) })).toContain('1 line split')
  })

  it('reads why an apply was refused', () => {
    const err = (status: number, message: string) => new ApiError(status, { code: 'x', message })
    expect(llmApplyProblem(err(422, 'Re-segmenting would clear translations, flags or notes on the lines being split -- pass confirm=true.'))).toBe('confirm')
    expect(llmApplyProblem(err(409, "This drama's lines changed since the preview -- run the preview again."))).toBe('changed')
    expect(llmApplyProblem(err(409, "This drama's lines changed since you loaded them -- reload and try again."))).toBe('changed')
    expect(llmApplyProblem(err(409, 'A background job is still running for this drama -- wait for it to finish.'))).toBe('job')
    expect(llmApplyProblem(err(409, 'A re-segmentation is already running for this drama.'))).toBe('job')
    expect(llmApplyProblem(err(404, 'No LLM re-segmentation preview is ready for this drama -- run the preview first.'))).toBe('gone')
    expect(llmApplyProblem(err(503, 'down'))).toBeNull()
    expect(llmApplyProblem(new Error('x'))).toBeNull()
    // The apply job's own error text (checked again at run time).
    expect(llmApplyProblem('Re-segmenting would now clear translations, flags or notes on the lines being split -- nothing was changed; apply again with confirm=true.')).toBe('confirm')
    expect(llmApplyProblem("This drama's lines changed since the preview -- nothing was changed; run the preview again.")).toBe('changed')
    expect(llmApplyProblem('Something else')).toBeNull()
  })

  it('never shows raw API wording for a refusal', () => {
    for (const p of ['confirm', 'changed', 'gone', 'job'] as const) {
      const text = llmApplyProblemText(p)
      expect(text).not.toMatch(/confirm=|use_preview|_/)
    }
    expect(llmApplyProblemText('job')).toBe(JOB_RUNNING_MESSAGE)
  })
})

describe('re-split summary', () => {
  const base = { split_lines: 31, lines_before: 260, line_count: 347, timing: 'proportional', speakers_reassigned: true }
  it('says how many lines became how many pieces', () => {
    expect(resplitSummary(base)).toBe('Split 31 lines into 118; speakers re-assigned.')
    expect(resplitSummary({ ...base, split_lines: 1, line_count: 262, speakers_reassigned: false })).toBe('Split 1 line into 3.')
  })
  it('adds aligned, cleared and note parts', () => {
    expect(resplitSummary({ ...base, timing: 'aligned', aligned_lines: 30, cleared_translations: 1, note: 'x' })).toBe(
      'Split 31 lines into 118; 30 timed from the audio; speakers re-assigned; 1 translation cleared. x',
    )
  })
  it('reports nothing to split', () => {
    expect(resplitSummary({ split_lines: 0 })).toMatch(/Nothing changed/)
  })
  it('summarises a dry run', () => {
    expect(resplitPreviewSummary({ dry_run: true, split_lines: 31, pieces: 118 })).toBe('Preview: 31 lines would be split into 118.')
    expect(resplitPreviewSummary({ split_lines: 1, pieces: 3, cleared_translations: 1 })).toBe(
      'Preview: 1 line would be split into 3. 1 translation would be cleared.',
    )
    expect(resplitPreviewSummary({ split_lines: 0, note: 'No line is over the limits at Normal sensitivity. Try "More".' })).toMatch(/Normal sensitivity/)
    expect(resplitPreviewSummary({ split_lines: 0 })).toBe('Preview: no line would be split.')
  })
  it('spots the confirm refusal only', () => {
    const e = (s: number, m: string) => new ApiError(s, { code: 'x', message: m })
    expect(resplitNeedsConfirm(e(422, 'pass confirm=true.'))).toBe(true)
    expect(resplitNeedsConfirm(e(409, 'pass confirm=true.'))).toBe(false)
    expect(resplitNeedsConfirm(new Error('confirm'))).toBe(false)
  })
})

describe('speaker time summary', () => {
  const sum = {
    speakers: [{ label: 'Anna', seconds: 220, percent: 84.6, turns: 41 }, { label: 'Bo', seconds: 40, percent: 15.4, turns: 1 }],
    total_speech_seconds: 260, uncovered_seconds: 3700,
  }
  it('lists each speaker', () => {
    expect(speakerTimeLines(sum)).toEqual(['Anna  3:40 · 84.6% · 41 turns', 'Bo  0:40 · 15.4% · 1 turn'])
  })
  it('footer mentions the uncovered audio only when known', () => {
    expect(speakerTimeFooter(sum)).toBe('4:20 of speech in the saved detection; 1:01:40 of the audio has no speaker turn.')
    expect(speakerTimeFooter({ ...sum, uncovered_seconds: null })).toBe('4:20 of speech in the saved detection.')
  })
})

describe('timingPatch', () => {
  const at = (idx: number, start: number, end: number) => mk(idx, { idx, start, end })
  const mid = at(1, 5, 7)
  const around = { prev: at(0, 1, 4), next: at(2, 8, 10) }

  it('is a normal expected-checked patch for one field', () => {
    expect(timingPatch(mid, around, 'start', 5.1)).toEqual({ start: 5.1, expected: { start: 5 } })
    expect(timingPatch(mid, around, 'end', 7.5)).toEqual({ end: 7.5, expected: { end: 7 } })
  })

  it('rounds away float noise and never goes below zero', () => {
    expect(timingPatch(mid, around, 'end', 7 + 0.1 + 0.2)).toEqual({ end: 7.3, expected: { end: 7 } })
    expect(timingPatch(at(0, 0.05, 2), {}, 'start', -0.05)).toEqual({ start: 0, expected: { start: 0.05 } })
  })

  it('refuses a start at or after the end, with the existing message', () => {
    expect(timingPatch(mid, around, 'start', 7)).toBe('End must be after start.')
    expect(timingPatch(mid, around, 'end', 5)).toBe('End must be after start.')
  })

  it('refuses to push into the previous or next line', () => {
    expect(timingPatch(mid, around, 'start', 3.9)).toBe('Start would overlap line #1.')
    expect(timingPatch(mid, around, 'end', 8.1)).toBe('End would overlap line #3.')
    expect(timingPatch(mid, around, 'start', 4)).toEqual({ start: 4, expected: { start: 5 } })
    expect(timingPatch(mid, around, 'end', 8)).toEqual({ end: 8, expected: { end: 7 } })
  })

  it('lets a line that already overlaps move out of the overlap', () => {
    const tight = at(1, 3, 7)
    expect(timingPatch(tight, around, 'start', 3.5)).toEqual({ start: 3.5, expected: { start: 3 } })
    expect(timingPatch(tight, around, 'start', 2.5)).toBe('Start would overlap line #1.')
  })

  it('ignores neighbours that are not adjacent in the script (filtered lists)', () => {
    const far = { prev: at(0, 1, 6), next: at(9, 5, 10) }
    expect(timingPatch(at(4, 5, 7), far, 'start', 4)).toEqual({ start: 4, expected: { start: 5 } })
    expect(timingPatch(at(4, 5, 7), far, 'end', 9)).toEqual({ end: 9, expected: { end: 7 } })
  })

  it('is null when nothing would change', () => {
    expect(timingPatch(mid, around, 'start', 5)).toBeNull()
  })
})

describe('split cut suggestions', () => {
  it('finds boundaries after spaces and punctuation, never at the edges', () => {
    expect(cutBoundaries('Hello there, friend')).toEqual([6, 13])
    expect(cutBoundaries('你好，朋友。')).toEqual([3])
    expect(cutBoundaries('no')).toEqual([])
    expect(cutBoundaries('a ')).toEqual([])
  })
  it('snaps to the nearest boundary, earlier on a tie', () => {
    expect(snapCut('Hello there, friend', 9)).toBe(6)
    expect(snapCut('Hello there, friend', 11)).toBe(13)
    expect(snapCut('aa bb', 2)).toBe(3)
  })
  it('keeps the exact offset when the text has no boundary, clamped inside', () => {
    expect(snapCut('abcdef', 3)).toBe(3)
    expect(snapCut('abcdef', 0)).toBe(1)
    expect(snapCut('abcdef', 99)).toBe(5)
    expect(snapCut('a', 1)).toBe(1)
  })
  it('counts emoji as one character', () => {
    expect(snapCut('😀😀 😀😀', 2)).toBe(3)
    expect(snapCut('😀😀😀😀', 2)).toBe(2)
  })
  it('cuts the translation at the same fraction as the source', () => {
    expect(proportionalCut(4, 2, 'Hello there, friend')).toBe(13)
    expect(proportionalCut(10, 7, 'one two three four five six')).toBe(19)
    expect(proportionalCut(0, 0, 'ab cd')).toBe(3)
  })
  it('steps to the previous or next boundary, else the edge', () => {
    expect(stepToBoundary('Hello there, friend', 6, 1)).toBe(13)
    expect(stepToBoundary('Hello there, friend', 6, -1)).toBe(1)
    expect(stepToBoundary('你好，朋友。你', 3, 1)).toBe(6)
    expect(stepToBoundary('abcdef', 2, 1)).toBe(5)
  })
})

describe('splitTranslationPieces', () => {
  it('trims the way the server does, leaving the cut itself alone', () => {
    expect(splitTranslationPieces('Thanks,  dear friends', 8)).toEqual(['Thanks,', 'dear friends'])
    expect(splitTranslationPieces(' Hi there ', 4)).toEqual([' Hi', 'there'])
    expect(splitPieces('Thanks,  dear friends', 8)).toEqual(['Thanks, ', ' dear friends'])
  })
})

describe('undo of a structural edit', () => {
  it('needs both the snapshot id and the fingerprint the server returned', () => {
    expect(undoHandleOf({ history_id: 7, lines_fingerprint: 'abc' })).toEqual({ historyId: 7, fingerprint: 'abc' })
    expect(undoHandleOf({ history_id: 7 })).toBeNull()
    expect(undoHandleOf({ history_id: null, lines_fingerprint: 'abc' })).toBeNull()
    expect(undoHandleOf({})).toBeNull()
  })
  it('explains a refused undo in plain text, keeping the offer only while a job runs', () => {
    const changed = new ApiError(409, { code: 'conflict', message: 'The lines were edited since that change' })
    expect(undoRefusal(changed)).toEqual({ text: UNDO_CHANGED_MESSAGE, keepOffer: false })
    expect(UNDO_CHANGED_MESSAGE).toContain('Records → Line history')
    // Told apart by the server's reason, not its wording, and never sent to Records,
    // whose restore would delete the note.
    const noted = new ApiError(409, { code: 'conflict', message: 'anything', details: { reason: 'notes_on_removed_lines' } })
    expect(undoRefusal(noted)).toEqual({ text: UNDO_NOTES_MESSAGE, keepOffer: false })
    expect(UNDO_NOTES_MESSAGE).not.toContain('Line history')
    expect(UNDO_NOTES_MESSAGE).toContain('move or copy the note first')
    const otherReason = new ApiError(409, { code: 'conflict', message: 'The lines were edited', details: { reason: 'other' } })
    expect(undoRefusal(otherReason)).toEqual({ text: UNDO_CHANGED_MESSAGE, keepOffer: false })
    const job = new ApiError(409, { code: 'conflict', message: 'A background job is still running' })
    expect(undoRefusal(job)).toEqual({ text: JOB_RUNNING_MESSAGE, keepOffer: true })
    expect(undoRefusal(new ApiError(404, { code: 'not_found', message: 'x' }))).toEqual({ text: UNDO_GONE_MESSAGE, keepOffer: false })
    expect(UNDO_GONE_MESSAGE).toContain('Records → Line history')
    expect(undoRefusal(new ApiError(500, { code: 'error', message: 'x' }))).toBeNull()
  })
  it('warns how many noted lines a Records restore would remove', () => {
    expect(restoreLossText(1)).toMatch(/^1 line this would remove has a note or emotion tag\. Restoring deletes them\./)
    expect(restoreLossText(3)).toMatch(/^3 lines this would remove have notes or emotion tags\./)
  })
  it('says what an undo of a delete or merge does not bring back', () => {
    expect(undoDoneMessage('delete')).toContain('but not its notes or emotion tag')
    expect(undoDoneMessage('merge')).toContain('notes and emotion tags stay on the line they were merged into, unless that line already had its own')
    expect(undoDoneMessage('split')).toBe('Undone. The lines are back as they were before.')
    expect(undoDoneMessage('resplit')).toBe(undoDoneMessage('split'))
  })
})
