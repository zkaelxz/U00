/** What the Simple view's check queue shows: which view it opens on, its counts, its reasons and its wording. */
import { humanize, humanizeValue } from '../../../../components/labels'
import { lineNumber } from '../../../../lineNumber'
import { capFirst, plural } from '../../../../labels'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
import { formatTime, RISK_CONFIDENCE_BELOW } from './reviewLogic'

// The queue is the server's risk view: translated, unchecked lines with a flag or a low Whisper score, riskiest first.
export const QUEUE_FILTER: LineFilter = 'risk'

/**
 * The view Review opens on. A link that names lines wins over the queue, so it never opens on a list without
 * the line it points at: ?focus=flagged opens Flagged, ?find= (a Ctrl+K line hit) and ?focus=all open All.
 */
export function initialLinesFilter(link: { focus?: string; find?: string } | null, simple: boolean): LineFilter {
  if (link?.focus === 'flagged') return 'flagged'
  if (!simple || link?.focus === 'all' || link?.find) return 'all'
  return QUEUE_FILTER
}

// Flags whose fix is a glossary entry: the term the English missed, or a name the AI check doubted.
const GLOSSARY_FLAGS = new Set(['glossary_missing', 'name_uncertain'])
export const opensGlossary = (line: Pick<ReviewLine, 'flag'>) => !!line.flag && GLOSSARY_FLAGS.has(line.flag)

/** Why an unflagged line is in the queue: only Whisper's score put it there. Null for a flagged line, which shows its flag. */
export function confidenceReason(line: Pick<ReviewLine, 'flag' | 'confidence'>): string | null {
  if (line.flag || line.confidence == null || line.confidence >= RISK_CONFIDENCE_BELOW) return null
  return `Whisper was unsure of this line (${Math.round(line.confidence * 100)}% sure)`
}

/** The flag as the line list has always written it: "Content blocked · gemini: SAFETY". */
export function flagText(line: Pick<ReviewLine, 'flag' | 'flag_note'>): string {
  return `${humanizeValue(line.flag)}${line.flag_note ? ` · ${capFirst(line.flag_note)}` : ''}`
}

/** A queue card's reason: the sentence the open card shows, and the few words of a closed card and the phone's Next line. */
export interface QueueReason {
  text: string
  short: string
}

// A note the server writes in a known shape becomes its own sentence (none: the kind's own sentence already says it all);
// null means "not that shape", never a part of it.
type ReadNote = (note: string) => { text?: string; short?: string } | null

interface FlagKind {
  text: string
  short: string
  read?: ReadNote
}

const UNSURE_SHORT = 'Whisper was unsure'
const NUM = '(\\d+(?:\\.\\d+)?)'

// "a", "a and b", "a, b and c"
const joinAnd = (parts: string[]) => (parts.length < 2 ? parts.join('') : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`)
const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1)

// Notes that are one fixed text on the server, each with the sentence that replaces it.
const fixedNotes = (notes: Record<string, string>): ReadNote => (note) => (note in notes ? { text: notes[note] } : null)
// A note whose words the kind's own sentence already covers.
const saidByKind = (matches: (note: string) => boolean): ReadNote => (note) => (matches(note) ? {} : null)

// engine_backends/glossary_check.py flag_note: "Glossary: 夷陵老祖 -> Yiling Patriarch; 魏婴 -> Wei Ying missing (+2 more)".
const readGlossary: ReadNote = (note) => {
  const m = /^Glossary: ([\s\S]+) missing(?: \(\+(\d+) more\))?$/.exec(note)
  if (!m) return null
  const pairs = m[1].split('; ').map((p) => p.split(' -> '))
  if (pairs.some((p) => p.length !== 2 || !p[0].trim() || !p[1].trim())) return null
  const more = Number(m[2] ?? 0)
  const names = pairs.map(([src]) => src).join(', ') + (more ? ` and ${more} more` : '')
  const writes = joinAnd([...pairs.map(([src, en]) => `${src} as “${en}”`), ...(more ? [`${more} more ${more === 1 ? 'name' : 'names'}`] : [])])
  const one = pairs.length === 1 && !more
  return {
    text: `The glossary writes ${writes}, but the English doesn’t use ${one ? 'that name' : 'those names'}. ` +
      `Put ${one ? 'it' : 'them'} in the English, or change the glossary if ${one ? 'the' : 'a'} name is wrong.`,
    short: `Glossary name not used: ${names}`,
  }
}

// subtitle_formats.py _dense_note: the speed, the limit, then either room to show it longer or "shorten the text".
const readSpeed: ReadNote = (note) => {
  const m = new RegExp(`^${NUM} characters/second -- more than ~${NUM}/s is hard to read in time\\. ([\\s\\S]+)$`).exec(note)
  if (!m) return null
  const room = new RegExp(`^The next line leaves room: it could be shown ${NUM} s longer, or shorten the text\\.$`).exec(m[3])
  const fix = m[3] === 'The speech is this fast: shorten the text.'
    ? 'Shorten the text.'
    : room ? `Shorten the text, or show the line ${room[1]} s longer: the next line leaves room.` : null
  if (!fix) return null
  return {
    text: `This subtitle is too fast to read: ${m[1]} characters a second, and more than about ${m[2]} is hard to read in time. ${fix}`,
    short: `Fast to read, ${Math.round(Number(m[1]))} characters a second`,
  }
}

// subtitle_formats.py overlap_note.
const readOverlap: ReadNote = (note) => {
  const m = new RegExp(`^Overlapped the next line by ${NUM}s; exports trim its end to ${NUM}s\\. Adjust the timing to fix it for good\\.$`).exec(note)
  return m ? { text: `This line runs ${m[1]} s into the next one, so exports end it early, at ${formatTime(Number(m[2]))}. Change its end time to fix it for good.` } : null
}

// timing_drift.py: the findings joined by "; ", first letter raised, a full stop, and the music caveat on a noisy file.
const MUSIC_CAVEAT = ' Speech detection can mistake music for speech; check by ear.'
const DRIFT_PARTS: [RegExp, (m: RegExpExecArray) => string][] = [
  [/^sits in silence \(no speech detected\)$/, () => 'no speech is heard during it'],
  [new RegExp(`^starts ${NUM} s before speech$`), (m) => `it starts ${m[1]} s before the speech`],
  [new RegExp(`^ends ${NUM} s after speech$`), (m) => `it ends ${m[1]} s after the speech`],
  [/^mostly silence \((\d+)% speech\)$/, (m) => `it is mostly silence (speech for ${m[1]}% of it)`],
  [new RegExp(`^overlaps the next line by ${NUM} s$`), (m) => `it runs ${m[1]} s into the next line`],
]
const readDrift: ReadNote = (note) => {
  const music = note.endsWith(MUSIC_CAVEAT)
  const body = music ? note.slice(0, -MUSIC_CAVEAT.length) : note
  if (!body.endsWith('.')) return null
  const parts = lowerFirst(body.slice(0, -1)).split('; ').map((part) => {
    for (const [re, say] of DRIFT_PARTS) {
      const m = re.exec(part)
      if (m) return say(m)
    }
    return null
  })
  if (parts.some((p) => p === null)) return null
  // "it starts … and ends …": the subject is said once.
  const said = (parts as string[]).map((p, i) => (i > 0 && parts[0]?.startsWith('it ') && p.startsWith('it ') ? p.slice(3) : p))
  return {
    text: `The times of this line don’t match the speech in the audio: ${joinAnd(said)}. Play it and move its start or end to the speech.` +
      (music ? ' Music can be taken for speech in this audio, so check by ear.' : ''),
  }
}

// auto_qc.py issue_note: one sentence per direction, then the advice.
const QC_ADVICE = ' Check the translation says the same thing.'
const QC_PARTS: [string, string][] = [
  ['In the source but not the translation: ', 'In the source but not the English: '],
  ['In the translation but not the source: ', 'In the English but not the source: '],
  ['Uses a translation flagged as prohibited: ', 'The English uses a translation the glossary says not to use: '],
]
const readQc: ReadNote = (note) => {
  if (!note.endsWith(QC_ADVICE)) return null
  const parts = note.slice(0, -QC_ADVICE.length).split(/(?<=\.) (?=In the source but not|In the translation but not|Uses a translation flagged)/)
  const said = parts.map((part) => {
    const kind = QC_PARTS.find(([from]) => part.startsWith(from))
    return kind && part.endsWith('.') ? kind[1] + part.slice(kind[0].length) : null
  })
  if (said.some((s) => s === null)) return null
  return { text: `The source and the English don’t agree on a number, date, name, amount or unit. ${said.join(' ')} Make the English say the same thing as the source.` }
}

// compare_transcription_service._disagreement_note, with its engine labels.
const OTHER_ENGINES = ['Whisper', 'Qwen3 ASR with speech detection', 'Qwen3 ASR on long windows', 'Qwen3 ASR', 'anime-whisper (Japanese, experimental, non-commercial)']
const readDisagree: ReadNote = (note) => {
  const engines = OTHER_ENGINES.map((e) => e.replace(/[()]/g, '\\$&')).join('|')
  const m = new RegExp(`^Heard differently \\((\\d+)% alike\\)\\. Saved: ([\\s\\S]*?) / (${engines}): ([\\s\\S]*)$`).exec(note)
  if (!m) return null
  return {
    text: `Two speech engines heard this line differently. The saved text is “${m[2]}”; ${m[3]} heard “${m[4]}”. ` +
      'Play it and keep the one that’s right, or transcribe the line again.',
  }
}

// source_suspect.py: its findings joined by "; " (the mid-phrase one holds a "; " of its own).
const SCRIPTS: Record<string, string> = { Kana: 'Japanese kana', Hangul: 'Korean letters', Han: 'Chinese characters', Latin: 'Latin letters', Other: 'unusual characters' }
const SUSPECT_PART = /(?:“([^”]*)” repeats (\d+)\+ times in a row|has ([A-Za-z/]+) characters|(is mostly Latin letters)|ends mid-phrase; the next line is only “([^”]*)”)(?:; |$)/y
const readSuspect: ReadNote = (note) => {
  if (note === 'Qwen3-ASR returned no text here; Whisper\'s text was kept') {
    return { text: 'Qwen3-ASR heard no words here, so Whisper’s text was kept. Play the line and check the source text.' }
  }
  const found: string[] = []
  SUSPECT_PART.lastIndex = 0
  while (SUSPECT_PART.lastIndex < note.length) {
    const m = SUSPECT_PART.exec(note)
    if (!m) return null
    if (m[1] !== undefined) found.push(`“${m[1]}” repeats ${m[2]} or more times in a row`)
    else if (m[3] !== undefined) {
      const names = m[3].split('/').map((s) => SCRIPTS[s])
      if (names.some((n) => !n)) return null
      found.push(`it has ${joinAnd(names)} in it`)
    } else if (m[4] !== undefined) found.push('it is mostly Latin letters')
    else found.push(`it stops mid-sentence, and the next line is only “${m[5]}”`)
  }
  if (found.length === 0) return null
  return {
    text: `The source text looks off: ${joinAnd(found)}. The transcription may be wrong or split badly, which can make the English odd. ` +
      'Play it and fix the source text, or transcribe the line again.',
  }
}

// translate_pipeline.py: "<engine>: <the engine's stated reason>".
const readBlocked: ReadNote = (note) => {
  const m = /^([\w.-]+): ([\s\S]+)$/.exec(note)
  if (!m) return null
  return {
    text: `The translation engine’s content filter refused this line, so it was not translated. ${humanize('engine', m[1])} gave this reason: “${m[2]}”. ` +
      'Retry the line with another engine.',
  }
}

// bulk_apply.py and bulk_reflect.py: which batch job lost its result.
const readBulk: ReadNote = (note) => {
  const lost = /^Bulk result not applied: ([\s\S]+)$/.exec(note)
  if (lost) {
    return { text: `The source text changed while a batch translation was waiting, so its English was not used. Translate this line again. The unused English was “${lost[1]}”.` }
  }
  return fixedNotes({
    'Source text changed while a bulk Reflect job was pending':
      'The source text changed while a batch review of the English was waiting, so its suggestion was not used. Check the English still matches the source.',
    'Source text changed while bulk flagging was pending -- re-run it':
      'The source text changed while a batch check was waiting, so its result was not used. Run the check again.',
    'Source text changed while bulk emotion tagging was pending':
      'The source text changed while batch emotion tagging was waiting, so its tag was not used. Tag the line again.',
  })(note)
}

// translation_guard.py's notes for a reply that came back wrong, and the line it gave up on.
const readTranslationCheck: ReadNote = (note) => {
  if (/^check: the model's reply for this line was unusable \(/.test(note)) {
    return { text: 'The translation engine’s answer for this line could not be used, so the English may be missing or wrong. Fix the English, or use Improve translation.' }
  }
  return fixedNotes({
    'check: may be merged or shifted':
      'The English may also hold the next line’s words, or belong to a different line. Compare it with the source and the lines around it.',
    'check: Chinese, Japanese or Korean characters left in the English':
      'The English still has Chinese, Japanese or Korean characters in it. Translate those words too.',
    'check: a number in the source is different or missing in the English':
      'A number in the source is different or missing in the English. Check the number.',
    'check: a chapter heading was added to this line':
      'A chapter heading that isn’t in the source was added to this line. Take it out of the English.',
    'check: metric measurement in the source reads as imperial here':
      'The source gives a metric measurement, but the English shows it in imperial units. Check the measurement.',
  })(note)
}

const TIMING_CHECK = ' Play it and check it starts and ends with the speech.'

// One entry per key of FLAG_REASONS and SYSTEM_FLAG_REASONS in engine_backends/flag_reasons.py.
const FLAG_KINDS: Record<string, FlagKind> = {
  uncertain_translation: {
    text: 'The English may not say what the source says, or may read awkwardly. Read both and fix the English if needed.',
    short: 'Translation may be off',
    read: readTranslationCheck,
  },
  ambiguous_reference: {
    text: 'It isn’t clear who or what a word like “she” or “that place” means here. Check the English points to the right person or thing.',
    short: 'Unclear who or what',
  },
  name_uncertain: {
    text: 'A name or term here may be misspelled, or written differently than in other lines. Check it against the glossary.',
    short: 'Name may be misspelled',
  },
  slang_idiom: {
    text: 'This line has slang or a saying that may not have come across well in English. Check the English sounds natural and means the same.',
    short: 'Slang or a saying',
  },
  timing_uncertain: {
    text: `The times of this line are a guess and may not match the speech.${TIMING_CHECK}`,
    short: 'Times are a guess',
    read: fixedNotes({
      'Forced alignment returned zero-length or out-of-order timing for this line, so it uses the approximate timing instead -- check it lines up.':
        `The app couldn’t match this line’s times to the speech, so it uses a rough guess.${TIMING_CHECK}`,
      'Forced alignment returned zero-length or out-of-order timing for part of this line; it was estimated from the neighbouring words -- check it lines up.':
        `Part of this line couldn’t be matched to the speech, so its times were worked out from the words around it.${TIMING_CHECK}`,
      'Split evenly by length (no sentence punctuation or word timings to cut at); the times are approximate.':
        `This line was split by length, with no punctuation or word timings to cut at, so its times are rough.${TIMING_CHECK}`,
    }),
  },
  language_uncertain: {
    text: 'The text doesn’t seem to be in the language heard for this line, even after a second try. Check the text, and the line’s spoken language under Edit details.',
    short: 'Language unclear',
    read: saidByKind((note) => note === 'The spoken language of this line couldn\'t be confirmed: the text doesn\'t match the language detected, and a retry in the title\'s language didn\'t match either -- check the text and the language.'),
  },
  timing_overlap: {
    text: 'This line runs into the next one, so exports end it early. Change its end time to fix it for good.',
    short: 'Runs into the next line',
    read: readOverlap,
  },
  timing_drift: {
    text: 'The times of this line don’t match the speech in the audio. Play it and move its start or end to the speech.',
    short: 'Times off the speech',
    read: readDrift,
  },
  reading_speed: {
    text: 'This subtitle is too fast to read: too many characters for the time it is shown. Shorten the text.',
    short: 'Fast to read',
    read: readSpeed,
  },
  factual_detail: {
    text: 'The source and the English don’t agree on a number, date, name, amount or unit. Make the English say the same thing as the source.',
    short: 'A number or name differs',
    read: readQc,
  },
  engines_disagree: {
    text: 'Two speech engines heard this line differently. Play it and keep the one that’s right, or transcribe the line again.',
    short: 'Heard two ways',
    read: readDisagree,
  },
  gap_untranscribed: {
    text: 'This line was added for a stretch of audio that had no subtitle. Play it and check the text matches what is said.',
    short: 'Added for a gap',
    read: saidByKind((note) => note === 'Added for a stretch with no subtitle line; check the text'),
  },
  bulk_source_changed: {
    text: 'The source text changed while a batch job was waiting, so its result was not used. Translate this line again.',
    short: 'Source changed during a batch',
    read: readBulk,
  },
  source_changed: {
    text: 'The source text was changed after this line was translated, so the English may no longer match. Keep the English if it still fits, or improve it.',
    short: 'Source changed',
  },
  pronoun_check: {
    text: 'The English says “he” or “him”, but this line’s speaker has no character set as he/him. Check the pronoun is right.',
    short: 'Check he or him',
    read: saidByKind((note) => note === 'Says he/him but no he/him character is set for this speaker; check the pronoun'),
  },
  glossary_missing: {
    text: 'The English doesn’t use a name the glossary sets for this line. Put the glossary’s name in the English, or change the glossary if it is wrong.',
    short: 'Glossary name not used',
    read: readGlossary,
  },
  source_mismatch: {
    text: 'The English reads unlike its source line. It may belong to the line before or after, leave something out, or answer a different line. Compare it with the lines around it.',
    short: 'May not match the source',
    read: saidByKind((note) => /^Source match: the English reads unlike its source line \(similarity [\d.]+\)$/.test(note)),
  },
  source_suspect: {
    text: 'The source text looks off: the transcription may be wrong or split badly, which can make the English odd. Play it and fix the source text, or transcribe the line again.',
    short: 'Source text looks off',
    read: readSuspect,
  },
  content_blocked: {
    text: 'The translation engine’s content filter refused this line, so it was not translated. Retry the line with another engine.',
    short: 'Blocked by the engine',
    read: readBlocked,
  },
}

/** Every flag kind the queue writes its own words for. */
export const QUEUE_FLAG_KINDS = Object.keys(FLAG_KINDS)

/**
 * Why a queue card is listed, in everyday words. A note the server writes in a known shape gives the specifics; any
 * other note is shown as it is after the plain sentence. An unknown flag keeps the line list's own text. Null for a
 * line with no flag that Whisper was sure of.
 */
export function queueReason(line: Pick<ReviewLine, 'flag' | 'flag_note' | 'confidence'>): QueueReason | null {
  if (!line.flag) {
    const unsure = confidenceReason(line)
    return unsure ? { text: unsure, short: UNSURE_SHORT } : null
  }
  const kind = FLAG_KINDS[line.flag]
  if (!kind) return { text: flagText(line), short: humanizeValue(line.flag) }
  const note = (line.flag_note ?? '').trim()
  const read = note && kind.read ? kind.read(note) : null
  if (read) return { text: read.text ?? kind.text, short: read.short ?? kind.short }
  return { text: note ? `${kind.text} Note: ${capFirst(note)}` : kind.text, short: kind.short }
}

/** The one existing line action that fits a card's reason, shown beside Play. */
export type QueueAction = 'glossary' | 'retranscribe' | 'retry' | 'snap' | 'keep'

const ACTION_BY_FLAG: Record<string, QueueAction> = {
  glossary_missing: 'glossary',
  name_uncertain: 'glossary',
  source_suspect: 'retranscribe',
  engines_disagree: 'retranscribe',
  content_blocked: 'retry',
  timing_uncertain: 'snap',
  timing_drift: 'snap',
  timing_overlap: 'snap',
  source_changed: 'keep',
}

export function queueAction(line: Pick<ReviewLine, 'flag' | 'confidence'>): QueueAction | null {
  if (line.flag) return ACTION_BY_FLAG[line.flag] ?? null
  return confidenceReason(line) ? 'retranscribe' : null
}

// The Next line runs on after a comma, so a reason's first word drops its capital; Whisper is a name and keeps it.
const nextReason = (line: ReviewLine) => {
  const short = queueReason(line)?.short ?? UNSURE_SHORT
  return short === UNSURE_SHORT ? short : lowerFirst(short)
}

/** The next two cards after the one open, for the phone's one-card view; null at the end of the list. */
export function nextText(shown: ReviewLine[], activeId: number | null): string | null {
  const at = shown.findIndex((l) => l.id === activeId)
  const next = shown.slice(at + 1, at + 3)
  if (at === -1 || next.length === 0) return null
  return `Next: ${next.map((l) => `line ${lineNumber(l.idx)}, ${nextReason(l)}`).join(' · ')}`
}

/**
 * The card to open once `id` is checked and has left the queue: the one after it. The last card of a page with more
 * pages behind it is followed by the line that reload brings up from the next page, which lands last; only the very
 * last card of the queue goes back to the one before it.
 */
export function nextAfterCheck(shown: ReviewLine[], id: number, morePages: boolean): number | 'last' | undefined {
  const at = shown.findIndex((l) => l.id === id)
  return shown[at + 1]?.id ?? (morePages ? 'last' : shown[at - 1]?.id)
}

/** "2 of 5 to look at": the open card's place in the whole queue, across pages. */
export function positionText(shown: ReviewLine[], activeId: number | null, page: number, pageSize: number, total: number): string | null {
  const at = shown.findIndex((l) => l.id === activeId)
  return at === -1 ? null : `${(page - 1) * pageSize + at + 1} of ${total} to look at`
}

export interface QueueCounts {
  all: number | null
  risk: number
  untranslated: number
  unreviewed: number
}

export const queueCounts = (page: ReviewLinesPage, all: number | null): QueueCounts => ({
  all, risk: page.risk_count ?? 0, untranslated: page.untranslated_count, unreviewed: page.unreviewed_count ?? 0,
})

/** Lines with English and no warning: not in the queue and not waiting for a translation. */
export const fineCount = (c: QueueCounts): number | null => (c.all === null ? null : Math.max(0, c.all - c.risk - c.untranslated))

export const fineText = (c: QueueCounts): string | null => {
  const fine = fineCount(c)
  return fine === null || c.all === null ? null : `${fine} of ${c.all} look fine`
}

/** Unchecked lines outside the queue: what Mark the rest would look at (its exact count is the server's). */
export const restUnchecked = (c: QueueCounts): number => Math.max(0, c.unreviewed - c.risk)

export function otherFineText(c: QueueCounts): string | null {
  const fine = fineCount(c)
  if (!fine || c.risk === 0) return null
  return fine === 1 ? 'The other line has no warnings.' : `The other ${fine} lines have no warnings.`
}

export const untranslatedText = (n: number): string | null =>
  n > 0 ? `${plural(n, 'line')} ${n === 1 ? 'has' : 'have'} no English yet.` : null

// The words Mark episode reviewed already uses for the same wait.
export const JOB_RUNNING_TEXT = 'Another job is running on this title. Try again when it finishes.'
export const UNSURE_FIRST = 'Look at the lines Whisper was unsure of first: this would mark them checked too.'
export const WHOLE_LIST_FIRST =
  'Work through the list until it fits on one page. Until then the app can’t tell whether this would also mark lines that still need a look.'

/**
 * Why Mark the rest can't run now, or null when it can. Mark episode reviewed skips flagged lines only, so an
 * unflagged low-score line in the queue would be marked unseen. The list counts can't tell those lines apart
 * (flagged_count also holds checked and untranslated lines), so the queue page itself is read: it must hold the
 * whole queue, every line flagged.
 */
export function markRestBlocker(queuePage: ReviewLinesPage, jobRunning: boolean): string | null {
  if (jobRunning) return JOB_RUNNING_TEXT
  if (queuePage.lines.length < queuePage.total) return WHOLE_LIST_FIRST
  if (queuePage.lines.some((l) => !l.flag)) return UNSURE_FIRST
  return null
}

export const checkedText = (line: Pick<ReviewLine, 'idx'>) => `Line #${lineNumber(line.idx)} marked checked.`
