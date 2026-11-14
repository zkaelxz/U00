/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

import type { ReviewLine, ReviewLinesPage } from '../../../../types/review'
import {
  confidenceReason, fineText, initialLinesFilter, JOB_RUNNING_TEXT, markRestBlocker, nextAfterCheck, nextText, opensGlossary, otherFineText,
  positionText, queueAction, queueCounts, queueReason, QUEUE_FLAG_KINDS, restUnchecked, UNSURE_FIRST, untranslatedText, WHOLE_LIST_FIRST,
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

// Each kind with a note written the way the server writes it (the module named beside it), and the words the card shows.
const REASONS: [string, string, string, string][] = [
  ['uncertain_translation', 'check: may be merged or shifted', // translation_guard.py FLAG_NOTE
    'The English may also hold the next line’s words, or belong to a different line. Compare it with the source and the lines around it.',
    'Translation may be off'],
  ['ambiguous_reference', 'Who "he" is here is unclear.',
    'It isn’t clear who or what a word like “she” or “that place” means here. Check the English points to the right person or thing. Note: Who "he" is here is unclear.',
    'Unclear who or what'],
  ['name_uncertain', 'Wei Ying or Wei Yin?',
    'A name or term here may be misspelled, or written differently than in other lines. Check it against the glossary. Note: Wei Ying or Wei Yin?',
    'Name may be misspelled'],
  ['slang_idiom', '',
    'This line has slang or a saying that may not have come across well in English. Check the English sounds natural and means the same.',
    'Slang or a saying'],
  ['timing_uncertain', 'Split evenly by length (no sentence punctuation or word timings to cut at); the times are approximate.', // resegment.py
    'This line was split by length, with no punctuation or word timings to cut at, so its times are rough. Play it and check it starts and ends with the speech.',
    'Times are a guess'],
  ['language_uncertain', "The spoken language of this line couldn't be confirmed: the text doesn't match the language detected, and a retry in the title's language didn't match either -- check the text and the language.", // mixed_language.py
    'The text doesn’t seem to be in the language heard for this line, even after a second try. Check the text, and the line’s spoken language under Edit details.',
    'Language unclear'],
  ['timing_overlap', 'Overlapped the next line by 0.35s; exports trim its end to 12.40s. Adjust the timing to fix it for good.', // subtitle_formats.overlap_note
    'This line runs 0.35 s into the next one, so exports end it early, at 0:12.40. Change its end time to fix it for good.',
    'Runs into the next line'],
  ['timing_drift', 'Starts 0.6 s before speech; ends 1.2 s after speech. Speech detection can mistake music for speech; check by ear.', // timing_drift.py
    'The times of this line don’t match the speech in the audio: it starts 0.6 s before the speech and ends 1.2 s after the speech. Play it and move its start or end to the speech. Music can be taken for speech in this audio, so check by ear.',
    'Times off the speech'],
  ['reading_speed', '25.2 characters/second -- more than ~17/s is hard to read in time. The speech is this fast: shorten the text.', // subtitle_formats._dense_note
    'This subtitle is too fast to read: 25.2 characters a second, and more than about 17 is hard to read in time. Shorten the text.',
    'Fast to read, 25 characters a second'],
  ['factual_detail', 'In the source but not the translation: 300 (number). Uses a translation flagged as prohibited: Wei Yin. Check the translation says the same thing.', // auto_qc.issue_note
    'The source and the English don’t agree on a number, date, name, amount or unit. In the source but not the English: 300 (number). The English uses a translation the glossary says not to use: Wei Yin. Make the English say the same thing as the source.',
    'A number or name differs'],
  ['engines_disagree', 'Heard differently (62% alike). Saved: 三百人 / Qwen3 ASR: 三白人', // compare_transcription_service._disagreement_note
    'Two speech engines heard this line differently. The saved text is “三百人”; Qwen3 ASR heard “三白人”. Play it and keep the one that’s right, or transcribe the line again.',
    'Heard two ways'],
  ['gap_untranscribed', 'Added for a stretch with no subtitle line; check the text', // transcribe_gap_service.GAP_FLAG_NOTE
    'This line was added for a stretch of audio that had no subtitle. Play it and check the text matches what is said.',
    'Added for a gap'],
  ['bulk_source_changed', 'Bulk result not applied: Three hundred people', // bulk_apply.py
    'The source text changed while a batch translation was waiting, so its English was not used. Translate this line again. The unused English was “Three hundred people”.',
    'Source changed during a batch'],
  ['source_changed', '',
    'The source text was changed after this line was translated, so the English may no longer match. Keep the English if it still fits, or improve it.',
    'Source changed'],
  ['pronoun_check', 'Says he/him but no he/him character is set for this speaker; check the pronoun', // translate_pipeline.py
    'The English says “he” or “him”, but this line’s speaker has no character set as he/him. Check the pronoun is right.',
    'Check he or him'],
  ['glossary_missing', 'Glossary: 夷陵老祖 -> Yiling Patriarch missing', // glossary_check.flag_note
    'The glossary writes 夷陵老祖 as “Yiling Patriarch”, but the English doesn’t use that name. Put it in the English, or change the glossary if the name is wrong.',
    'Glossary name not used: 夷陵老祖'],
  ['source_mismatch', 'Source match: the English reads unlike its source line (similarity 0.42)', // alignment_check.flag_note
    'The English reads unlike its source line. It may belong to the line before or after, leave something out, or answer a different line. Compare it with the lines around it.',
    'May not match the source'],
  ['source_suspect', '“哈” repeats 4+ times in a row; ends mid-phrase; the next line is only “吗”', // source_suspect.py
    'The source text looks off: “哈” repeats 4 or more times in a row and it stops mid-sentence, and the next line is only “吗”. The transcription may be wrong or split badly, which can make the English odd. Play it and fix the source text, or transcribe the line again.',
    'Source text looks off'],
  ['content_blocked', 'gemini: SAFETY', // translate_pipeline.py
    'The translation engine’s content filter refused this line, so it was not translated. Gemini gave this reason: “SAFETY”. Retry the line with another engine.',
    'Blocked by the engine'],
]

describe('queue reasons', () => {
  it('has words for every flag kind the server can set', () => {
    const py = readFileSync(new URL('../../../../../../engine_backends/flag_reasons.py', import.meta.url), 'utf8')
    const kinds = [...py.matchAll(/^\s+"(\w+)":/gm)].map((m) => m[1])
    expect(kinds.length).toBeGreaterThan(15)
    expect([...QUEUE_FLAG_KINDS].sort()).toEqual([...kinds].sort())
    expect(REASONS.map(([kind]) => kind).sort()).toEqual([...kinds].sort())
  })

  for (const [flag, note, text, short] of REASONS) {
    it(`writes ${flag} as a plain sentence`, () => {
      expect(queueReason({ flag, flag_note: note, confidence: null })).toEqual({ text, short })
      expect(text).not.toMatch(/--|->/)
    })
  }

  it('reads the other shapes a note can take', () => {
    const say = (flag: string, flag_note: string) => queueReason({ flag, flag_note, confidence: null })
    expect(say('glossary_missing', 'Glossary: 夷陵老祖 -> Yiling Patriarch; 魏婴 -> Wei Ying; 蓝湛 -> Lan Zhan missing (+2 more)')).toEqual({
      text: 'The glossary writes 夷陵老祖 as “Yiling Patriarch”, 魏婴 as “Wei Ying”, 蓝湛 as “Lan Zhan” and 2 more names, but the English doesn’t use those names. ' +
        'Put them in the English, or change the glossary if a name is wrong.',
      short: 'Glossary name not used: 夷陵老祖, 魏婴, 蓝湛 and 2 more',
    })
    expect(say('reading_speed', '21.0 characters/second -- more than ~17/s is hard to read in time. The next line leaves room: it could be shown 0.8 s longer, or shorten the text.')?.text)
      .toBe('This subtitle is too fast to read: 21.0 characters a second, and more than about 17 is hard to read in time. Shorten the text, or show the line 0.8 s longer: the next line leaves room.')
    expect(say('timing_drift', 'Sits in silence (no speech detected).')?.text)
      .toBe('The times of this line don’t match the speech in the audio: no speech is heard during it. Play it and move its start or end to the speech.')
    expect(say('source_suspect', 'has Kana/Hangul characters')?.text).toContain('The source text looks off: it has Japanese kana and Korean letters in it.')
    expect(say('source_suspect', "Qwen3-ASR returned no text here; Whisper's text was kept")?.text)
      .toBe('Qwen3-ASR heard no words here, so Whisper’s text was kept. Play the line and check the source text.')
    expect(say('uncertain_translation', "check: the model's reply for this line was unusable (empty): ")?.text)
      .toBe('The translation engine’s answer for this line could not be used, so the English may be missing or wrong. Fix the English, or use Improve translation.')
    expect(say('timing_uncertain', '')?.text).toBe('The times of this line are a guess and may not match the speech. Play it and check it starts and ends with the speech.')
  })

  it('never shows half a parse: a note in another shape follows the plain sentence as it is', () => {
    const glossary = queueReason({ flag: 'glossary_missing', flag_note: 'Glossary: a -> b -> c missing', confidence: null })
    expect(glossary).toEqual({
      text: 'The English doesn’t use a name the glossary sets for this line. Put the glossary’s name in the English, or change the glossary if it is wrong. Note: Glossary: a -> b -> c missing',
      short: 'Glossary name not used',
    })
    expect(queueReason({ flag: 'timing_drift', flag_note: 'Starts 0.6 s before speech; wobbles.', confidence: null })?.text).toMatch(/speech\. Note: Starts 0\.6 s before speech; wobbles\.$/)
    expect(queueReason({ flag: 'source_suspect', flag_note: 'has Runic characters', confidence: null })?.text).toMatch(/Note: Has Runic characters$/)
    expect(queueReason({ flag: 'engines_disagree', flag_note: 'Heard differently (62% alike). Saved: 三百人 / qwen9: 三白人', confidence: null })?.short).toBe('Heard two ways')
  })

  it('keeps the line list’s own text for a kind it does not know, and the Whisper reason for an unflagged line', () => {
    expect(queueReason({ flag: 'brand_new_check', flag_note: 'look here', confidence: null })).toEqual({ text: 'Brand new check · Look here', short: 'Brand new check' })
    expect(queueReason({ flag: null, flag_note: null, confidence: 0.5 })).toEqual({ text: 'Whisper was unsure of this line (50% sure)', short: 'Whisper was unsure' })
    expect(queueReason({ flag: null, flag_note: null, confidence: 0.9 })).toBeNull()
  })
})

describe('the action that fits the reason', () => {
  const fit = (flag: string | null, confidence: number | null = null) => queueAction({ flag, confidence })

  it('offers the glossary for names, Re-transcribe for doubtful source text, the retry for a blocked line, Snap for timing and Keep English for a changed source', () => {
    expect(fit('glossary_missing')).toBe('glossary')
    expect(fit('name_uncertain')).toBe('glossary')
    expect(fit('source_suspect')).toBe('retranscribe')
    expect(fit('engines_disagree')).toBe('retranscribe')
    expect(fit(null, 0.4)).toBe('retranscribe')
    expect(fit('content_blocked')).toBe('retry')
    for (const flag of ['timing_uncertain', 'timing_drift', 'timing_overlap']) expect(fit(flag)).toBe('snap')
    expect(fit('source_changed')).toBe('keep')
  })

  it('offers nothing extra where no existing action fits', () => {
    for (const flag of ['uncertain_translation', 'ambiguous_reference', 'slang_idiom', 'language_uncertain', 'reading_speed', 'factual_detail',
      'gap_untranscribed', 'bulk_source_changed', 'pronoun_check', 'source_mismatch', 'brand_new_check']) expect(fit(flag), flag).toBeNull()
    expect(fit(null, 0.9)).toBeNull()
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
    expect(nextText(shown, 24)).toBe('Next: line 12, fast to read · line 7, Whisper was unsure')
    expect(nextText(shown, 7)).toBe('Next: line 9, times off the speech')
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
