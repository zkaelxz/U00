import { describe, expect, it } from 'vitest'

import type { ReviewLine, ReviewLinesPage } from '../../../../types/review'
import {
  confidenceReason, fineText, initialLinesFilter, JOB_RUNNING_TEXT, markRestBlocker, nextAfterCheck, nextText, opensGlossary, otherFineText,
  positionText, queueCounts, restUnchecked, UNSURE_FIRST, untranslatedText, WHOLE_LIST_FIRST,
} from './checkQueueModel'

const line = (id: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx: id - 1, start: id, end: id + 1, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, ...over,
})

const page = (lines: ReviewLine[], over: Partial<ReviewLinesPage> = {}): ReviewLinesPage => ({
  lines, page: 1, page_size: 40, total: lines.length, flagged_count: 0, untranslated_count: 0,
  unreviewed_count: 0, shaky_count: 0, risk_count: lines.length, ...over,
})

describe('initialLinesFilter', () => {
  it('opens Simple on the queue and Full on All', () => {
    expect(initialLinesFilter(null, true)).toBe('risk')
    expect(initialLinesFilter({}, true)).toBe('risk')
    expect(initialLinesFilter(null, false)).toBe('all')
    expect(initialLinesFilter({}, false)).toBe('all')
  })

  it('lets a link that names lines win over the queue, so it never opens on a list without its line', () => {
    // A Ctrl+K line hit links with ?find=; a search covers every line, not just the queue.
    expect(initialLinesFilter({ find: '夷陵老祖' }, true)).toBe('all')
    expect(initialLinesFilter({ focus: 'flagged' }, true)).toBe('flagged')
    expect(initialLinesFilter({ focus: 'all' }, true)).toBe('all')
    expect(initialLinesFilter({ focus: 'flagged' }, false)).toBe('flagged')
    // A link to a Review tool or section keeps the queue: it names no line.
    expect(initialLinesFilter({ focus: 'compare' }, true)).toBe('risk')
  })
})

describe('reasons', () => {
  it('spells out why an unflagged line is listed, and leaves a flagged line to its flag', () => {
    expect(confidenceReason(line(1, { confidence: 0.72 }))).toBe('Whisper was unsure of this line (72% sure)')
    expect(confidenceReason(line(1, { confidence: 0.4 }))).toBe('Whisper was unsure of this line (40% sure)')
    expect(confidenceReason(line(1, { confidence: 0.8 }))).toBeNull()
    expect(confidenceReason(line(1, { confidence: null }))).toBeNull()
    expect(confidenceReason(line(1, { confidence: 0.3, flag: 'reading_speed' }))).toBeNull()
  })

  it('offers the glossary for a missed glossary term or a doubted name only', () => {
    expect(opensGlossary(line(1, { flag: 'glossary_missing' }))).toBe(true)
    expect(opensGlossary(line(1, { flag: 'name_uncertain' }))).toBe(true)
    expect(opensGlossary(line(1, { flag: 'reading_speed' }))).toBe(false)
    expect(opensGlossary(line(1))).toBe(false)
  })
})

describe('the phone card', () => {
  const shown = [line(24, { flag: 'glossary_missing' }), line(12, { flag: 'reading_speed' }), line(7, { confidence: 0.5 }), line(9, { flag: 'timing_drift' })]

  it('says where the open card is in the whole queue', () => {
    expect(positionText(shown, 24, 1, 40, 4)).toBe('1 of 4 to look at')
    expect(positionText(shown, 12, 2, 40, 44)).toBe('42 of 44 to look at')
    expect(positionText(shown, 99, 1, 40, 4)).toBeNull()
  })

  it('names the next two cards with their reason, and nothing after the last', () => {
    expect(nextText(shown, 24)).toBe('Next: line 12, reading speed · line 7, Whisper was unsure')
    expect(nextText(shown, 7)).toBe('Next: line 9, timing drift')
    expect(nextText(shown, 9)).toBeNull()
    expect(nextText(shown, null)).toBeNull()
  })
})

describe('the card after a check', () => {
  const shown = [line(24), line(12), line(7)]

  it('is the one after the checked card', () => {
    expect(nextAfterCheck(shown, 24, false)).toBe(12)
    expect(nextAfterCheck(shown, 12, true)).toBe(7)
  })

  it('is the line the next page sends up when the last card of a page is checked, not the card before it', () => {
    expect(nextAfterCheck(shown, 7, true)).toBe('last')
  })

  it('is the card before only at the very end of the queue, and none when the queue empties', () => {
    expect(nextAfterCheck(shown, 7, false)).toBe(12)
    expect(nextAfterCheck([line(7)], 7, false)).toBeUndefined()
  })
})

describe('counts', () => {
  it('counts neither the queue nor the untranslated lines as fine', () => {
    const c = queueCounts(page([line(1)], { risk_count: 3, untranslated_count: 2, unreviewed_count: 10 }), 24)
    expect(fineText(c)).toBe('19 of 24 look fine')
    expect(otherFineText(c)).toBe('The other 19 lines have no warnings.')
    expect(restUnchecked(c)).toBe(7)
    expect(fineText(queueCounts(page([]), null))).toBeNull()
  })

  it('has no "other" lines to speak of when the queue is empty', () => {
    expect(otherFineText(queueCounts(page([], { risk_count: 0 }), 24))).toBeNull()
    expect(otherFineText(queueCounts(page([line(1)], { risk_count: 1 }), 2))).toBe('The other line has no warnings.')
  })

  it('says how many lines still have no English', () => {
    expect(untranslatedText(0)).toBeNull()
    expect(untranslatedText(1)).toBe('1 line has no English yet.')
    expect(untranslatedText(3)).toBe('3 lines have no English yet.')
  })
})

describe('markRestBlocker', () => {
  it('allows Mark the rest only when the whole queue is on the page and every line in it is flagged', () => {
    expect(markRestBlocker(page([line(1, { flag: 'reading_speed' }), line(2, { flag: 'glossary_missing' })]), false)).toBeNull()
    expect(markRestBlocker(page([]), false)).toBeNull()
  })

  it('refuses while an unflagged low-score line is listed: Mark episode reviewed would mark it unseen', () => {
    expect(markRestBlocker(page([line(1, { flag: 'reading_speed' }), line(2, { confidence: 0.5 })]), false)).toBe(UNSURE_FIRST)
  })

  it('refuses while the queue runs past this page, where its lines cannot be seen', () => {
    expect(markRestBlocker(page([line(1, { flag: 'reading_speed' })], { total: 41 }), false)).toBe(WHOLE_LIST_FIRST)
  })

  it('waits for a running job', () => {
    expect(markRestBlocker(page([]), true)).toBe(JOB_RUNNING_TEXT)
  })
})
