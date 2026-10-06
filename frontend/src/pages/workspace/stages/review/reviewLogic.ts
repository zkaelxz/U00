import { ApiError } from '../../../../api/client'
import type { SpeakerTimeSummary } from '../../../../types/workspace'
import type { JobRecord } from '../../../../types/jobs'
import type { ResegmentPreview, ResplitResult, ResplitSensitivity } from '../../../../types/restructure'
import type { TranslateEngine } from '../../../../types/translate'
import type { TranslateRunConfig } from '../../../../types/translateStage'
import { humanize } from '../../../../components/labels'
import { reflectAvailable } from '../../translateForm'
import { spendText } from './reviewResults'
import type { LineFilter, LinePatch, ReviewLine, ReviewMatch } from '../../../../types/review'
import { lineNumber } from '../../../../lineNumber'
import { languageLabel } from '../../../../labels'

export interface LineDraft {
  zh: string
  en: string
  speaker: string
  start: string
  end: string
  sfx: boolean
  // '' = the drama's source language.
  lang: string
}

// What one line's spoken language may be (core.LINE_LANGUAGES).
export const LINE_LANGUAGES = ['zh', 'ja', 'ko', 'en'] as const

// The row chip ("KO"): only for a line spoken in another language than the
// drama's, so a single-language drama shows nothing new.
export function lineLangChip(lang: string | null | undefined, sourceLanguage: string | null | undefined): string | null {
  if (!lang || lang === (sourceLanguage || 'zh')) return null
  return lang.toUpperCase()
}

// "Set language" in the line sheet: just this line, or every line of its speaker.
export type LanguageScope = 'line' | 'speaker'

export function languageSetText(updated: number, lang: string, sourceLanguage: string | null | undefined): string {
  const label = lang ? languageLabel(lang) : `the title default (${languageLabel(sourceLanguage || 'zh')})`
  if (updated === 0) return `Nothing changed: already ${label}.`
  return `Set ${updated} line${updated === 1 ? '' : 's'} to ${label}.`
}

export function titleDefaultLabel(sourceLanguage: string | null | undefined): string {
  return `Title default (${languageLabel(sourceLanguage || 'zh')})`
}

export const PAGE_SIZE = 40

export function draftFromLine(line: ReviewLine): LineDraft {
  return {
    zh: line.zh,
    en: line.en,
    speaker: line.speaker ?? '',
    start: String(line.start),
    end: String(line.end),
    sfx: line.sfx,
    lang: line.lang ?? '',
  }
}

// Only fields that differ are sent, each with the old value it was loaded with
// so the server can refuse (409) if someone else changed that field meanwhile.
// Returns a message when the draft is invalid, null when nothing changed.
export function buildPatch(line: ReviewLine, draft: LineDraft): LinePatch | string | null {
  const patch: LinePatch = {}
  const expected: Record<string, unknown> = {}
  for (const key of ['zh', 'en'] as const) {
    if (draft[key] !== line[key]) {
      patch[key] = draft[key]
      expected[key] = line[key]
    }
  }
  if (draft.speaker.trim() !== (line.speaker ?? '')) {
    patch.speaker = draft.speaker.trim()
    expected.speaker = line.speaker
  }
  if (draft.sfx !== line.sfx) {
    patch.sfx = draft.sfx
    expected.sfx = line.sfx
  }
  if (draft.lang !== (line.lang ?? '')) {
    patch.lang = draft.lang
    expected.lang = line.lang ?? ''
  }
  for (const key of ['start', 'end'] as const) {
    if (draft[key].trim() === '' || Number.isNaN(Number(draft[key]))) return `Enter a number for ${key}.`
    const n = Number(draft[key])
    if (n !== line[key]) {
      patch[key] = n
      expected[key] = line[key]
    }
  }
  if (Object.keys(patch).length === 0) return null
  if ((patch.end ?? line.end) <= (patch.start ?? line.start)) return 'End must be after start.'
  patch.expected = expected
  return patch
}

export type TimingField = 'start' | 'end'
type Timed = Pick<ReviewLine, 'idx' | 'start' | 'end'>

// A timing hotkey as a normal line patch. Uses buildPatch's end-after-start
// rule, and refuses to push a boundary into a neighbouring line (only when the
// move makes the overlap worse, so a line that already overlaps can be pulled out).
// Neighbours count only when adjacent in the script: a filtered or searched
// list can put unrelated lines side by side.
export function timingPatch(
  line: ReviewLine,
  neighbours: { prev?: Timed | null; next?: Timed | null },
  field: TimingField,
  seconds: number,
): LinePatch | string | null {
  const value = Math.max(0, Math.round(seconds * 1000) / 1000)
  const { prev, next } = neighbours
  if (field === 'start' && prev && prev.idx === line.idx - 1 && value < prev.end && value < line.start) {
    return `Start would overlap line #${lineNumber(prev.idx)}.`
  }
  if (field === 'end' && next && next.idx === line.idx + 1 && value > next.start && value > line.end) {
    return `End would overlap line #${lineNumber(next.idx)}.`
  }
  return buildPatch(line, { ...draftFromLine(line), [field]: String(value) })
}

// A draft that would send something (or is invalid) is dirty: navigation saves it first.
export function isDirty(line: ReviewLine, draft: LineDraft): boolean {
  return buildPatch(line, draft) !== null
}

export function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  return `${m}:${(seconds - m * 60).toFixed(2).padStart(5, '0')}`
}

// Whole seconds for a media duration ("24:10", "1:02:03").
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '–'
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

export function pageCount(total: number, pageSize: number = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / pageSize))
}

/** 1-based page holding the line at 0-based position `pos`. */
export function pageForPosition(pos: number, pageSize: number = PAGE_SIZE): number {
  return Math.floor(Math.max(0, pos) / pageSize) + 1
}

export const CONFLICT_MESSAGE = 'This line changed elsewhere. Reload and try again.'

// Stale ids from an apply, shown as the line numbers the user sees.
export function staleLabels(staleIds: number[], matches: ReviewMatch[]): string[] {
  return staleIds.map((id) => {
    const m = matches.find((x) => x.id === id)
    return m ? `#${lineNumber(m.idx)}` : `line id ${id}`
  })
}

export const AI_UNAVAILABLE_MESSAGE =
  'AI help is not set up. Add an API key under Settings, then try again.'
export const AI_STALE_MESSAGE = 'The line changed after this suggestion was made. Ask again.'

// A suggestion is applied like any other edit: only "en" is sent, with the
// value it was made against as the expected old value (a mismatch is a 409).
export function suggestionPatch(line: ReviewLine, suggestion: string): LinePatch | null {
  if (suggestion === line.en) return null
  return { en: suggestion, expected: { en: line.en } }
}

// The line's panel slot: the AI panel (LineAi) or a study tool (LineTools, R17-R19).
export type ToolMode = 'alternatives' | 'grammar' | 'pronounce'
export type PanelMode = 'improve' | 'explain' | ToolMode

export const isToolMode = (m: PanelMode): m is ToolMode =>
  m === 'alternatives' || m === 'grammar' || m === 'pronounce'

// The suggestion was made for current_en; if the row shows something else now
// it is stale and must not be applied.
export function suggestionIsStale(line: ReviewLine, currentEn: string): boolean {
  return line.en !== currentEn
}

// ---- active line and navigation ----

/** First flagged line, else first untranslated, else the first line. */
export function initialActiveId(lines: ReviewLine[]): number | null {
  const pick =
    lines.find((l) => l.flag) ?? lines.find((l) => l.zh.trim() && !l.en.trim()) ?? lines[0]
  return pick ? pick.id : null
}

type Step = { id: number } | { page: 'next' | 'prev' } | null

/** The line `delta` rows away from `activeId`, or a page change at an edge. */
export function stepFrom(lines: ReviewLine[], activeId: number | null, delta: 1 | -1, canPage: { next: boolean; prev: boolean }): Step {
  if (lines.length === 0) return null
  const i = lines.findIndex((l) => l.id === activeId)
  if (i === -1) return { id: (delta > 0 ? lines[0] : lines[lines.length - 1]).id }
  const j = i + delta
  if (j >= 0 && j < lines.length) return { id: lines[j].id }
  if (delta > 0 && canPage.next) return { page: 'next' }
  if (delta < 0 && canPage.prev) return { page: 'prev' }
  return null
}

/** Next/previous flagged line on the page after `fromIndex` (-1 = from the edge). */
export function nextFlaggedId(lines: ReviewLine[], fromIndex: number, delta: 1 | -1): number | null {
  if (fromIndex === -1) {
    const pick = delta > 0 ? lines.find((l) => l.flag) : [...lines].reverse().find((l) => l.flag)
    return pick ? pick.id : null
  }
  for (let j = fromIndex + delta; j >= 0 && j < lines.length; j += delta) if (lines[j].flag) return lines[j].id
  return null
}

/**
 * Where previous/next flagged looks (R08). A page of the "all" or "flagged"
 * view holds every flagged line near the start row, so it is searched first,
 * then the server from the page's edge. A page of a filtered view
 * ("untranslated") leaves flagged lines out, so the server is asked from the
 * focused row itself and none in between is skipped. `fromId` null = from
 * the far end.
 */
export function flaggedStep(
  lines: ReviewLine[],
  filter: LineFilter,
  fromIndex: number,
  delta: 1 | -1,
): { id: number } | { fromId: number | null } {
  if (filter !== 'all' && filter !== 'flagged' && fromIndex !== -1) return { fromId: lines[fromIndex].id }
  const id = nextFlaggedId(lines, fromIndex, delta)
  if (id !== null) return { id }
  const edge = delta > 0 ? lines[lines.length - 1] : lines[0]
  return { fromId: edge?.id ?? null }
}

// ---- structure edits ----

/** UTF-16 caret offset -> code-point offset (the server counts Python characters). */
export function codePointOffset(text: string, utf16Offset: number): number {
  return Array.from(text.slice(0, Math.max(0, utf16Offset))).length
}

/** The two pieces a split at code-point offset `at` makes. */
export function splitPieces(text: string, at: number): [string, string] {
  const chars = Array.from(text)
  return [chars.slice(0, at).join(''), chars.slice(at).join('')]
}

/** The translation pieces the server stores for a cut at `at`: it trims the
 *  first piece's end and the second piece's both ends (services/restructure_service.split_line). */
export function splitTranslationPieces(text: string, at: number): [string, string] {
  const [first, second] = splitPieces(text, at)
  return [first.trimEnd(), second.trim()]
}

export function charCount(text: string): number {
  return Array.from(text).length
}

const BREAK_PUNCT = new Set(Array.from(',.!?;:，。！？；：、…'))

/**
 * Code-point offsets where a cut reads naturally: right after a space run or
 * punctuation, before the next word. A cut that would leave either piece empty
 * is never offered.
 */
export function cutBoundaries(text: string): number[] {
  const chars = Array.from(text)
  const out: number[] = []
  for (let k = 1; k < chars.length; k++) {
    const prev = chars[k - 1]
    if ((/\s/.test(prev) || BREAK_PUNCT.has(prev)) && !/\s/.test(chars[k])) out.push(k)
  }
  return out
}

/** The boundary nearest `target` (ties go earlier); `target` itself when the text has none. */
export function snapCut(text: string, target: number): number {
  const len = charCount(text)
  const clamped = Math.min(Math.max(1, target), Math.max(1, len - 1))
  let best = clamped
  let bestDist = Infinity
  for (const k of cutBoundaries(text)) {
    const d = Math.abs(k - clamped)
    if (d < bestDist) {
      best = k
      bestDist = d
    }
  }
  return best
}

/** Where to cut the translation so it breaks at the same fraction of the text as the source did. */
export function proportionalCut(zhLen: number, zhAt: number, en: string): number {
  const enLen = charCount(en)
  const frac = zhLen > 0 ? zhAt / zhLen : 0.5
  return snapCut(en, Math.round(enLen * frac))
}

/** The previous (-1) or next (1) boundary from `at`, or the line's edge when there is none. */
export function stepToBoundary(text: string, at: number, dir: -1 | 1): number {
  const bounds = cutBoundaries(text)
  const hit = dir === 1 ? bounds.find((k) => k > at) : [...bounds].reverse().find((k) => k < at)
  const len = charCount(text)
  return hit ?? (dir === 1 ? Math.max(1, len - 1) : 1)
}

/** A cut time in proportion to the text before the split (the server's default is similar). */
export function estimateSplitTime(line: Pick<ReviewLine, 'start' | 'end' | 'zh'>, at: number): number {
  const n = charCount(line.zh)
  const frac = n > 0 ? Math.min(1, Math.max(0, at / n)) : 0.5
  return Math.round((line.start + (line.end - line.start) * frac) * 100) / 100
}

/** Default timing for a line added after `line` (null = at the start), before `next`. */
export function gapForNewLine(line: Pick<ReviewLine, 'end'> | null, next: Pick<ReviewLine, 'start'> | null): { start: number; end: number } {
  const start = line ? line.end : 0
  const room = next ? next.start - start : Infinity
  const end = room >= 0.5 ? Math.min(start + 2, next ? next.start : start + 2) : start + 2
  return { start: round2(start), end: round2(end) }
}

const round2 = (n: number) => Math.round(n * 100) / 100

/**
 * Interim guard until a line-index endpoint exists: before a structure edit the
 * whole id list is re-read; the edit only goes ahead when the loaded page still
 * sits where it was (filter "all": same ids at the same positions; other views:
 * the same ids, still in the same order).
 */
export function pageStillMatches(allIds: number[], pageIds: number[], filter: LineFilter | 'search', page: number, pageSize: number = PAGE_SIZE): boolean {
  if (filter === 'all') {
    const start = (page - 1) * pageSize
    const slice = allIds.slice(start, start + pageIds.length)
    return slice.length === pageIds.length && slice.every((id, i) => id === pageIds[i])
  }
  let last = -1
  for (const id of pageIds) {
    const pos = allIds.indexOf(id)
    if (pos <= last) return false
    last = pos
  }
  return true
}

/** `count` adjacent ids starting at `fromId`, or null when there are not enough. */
export function adjacentRun(allIds: number[], fromId: number, count: number): number[] | null {
  const i = allIds.indexOf(fromId)
  if (i === -1 || count < 2 || i + count > allIds.length) return null
  return allIds.slice(i, i + count)
}

/** Joined text as the server's merge builds it (zh concatenated, en space-joined). */
export function mergedText(lines: Pick<ReviewLine, 'zh' | 'en'>[]): { zh: string; en: string } {
  let zh = ''
  let en = ''
  lines.forEach((l, i) => {
    zh = i === 0 ? l.zh : zh.trimEnd() + l.zh.trim()
    en = i === 0 ? l.en : (en.trimEnd() + ' ' + l.en.trim()).trim()
  })
  return { zh, en }
}

export const MAX_MERGE_LINES = 50

export const LINES_CHANGED_MESSAGE = 'Lines changed since this page loaded. Reload and try again.'
export const JOB_RUNNING_MESSAGE = 'A job is running on this drama. Structure edits wait until it finishes.'

/** What the server gave back to undo one structural change (the snapshot taken before it, and
 *  a fingerprint of the lines it left, so an undo refuses if they were edited since). */
export interface UndoHandle {
  historyId: number
  fingerprint: string
}

export function undoHandleOf(r: { history_id?: number | null; lines_fingerprint?: string | null }): UndoHandle | null {
  return r.history_id && r.lines_fingerprint ? { historyId: r.history_id, fingerprint: r.lines_fingerprint } : null
}

export const UNDO_CHANGED_MESSAGE = 'The lines changed since. Restore from Records → Line history instead.'
export const UNDO_DONE_MESSAGE = 'Undone. The lines are back as they were before.'

/** Plain text for a refused undo, or null to show the generic banner. */
export function undoErrorText(e: unknown): string | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null
  return /job/i.test(e.message) ? JOB_RUNNING_MESSAGE : UNDO_CHANGED_MESSAGE
}

/** Plain text for a structure-edit failure, or null to show the generic banner. */
export function structureErrorText(e: unknown): string | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null
  return /job/i.test(e.message) ? JOB_RUNNING_MESSAGE : LINES_CHANGED_MESSAGE
}

// Interim until JobRecord carries drama_id: job ids are "<kind>_<dramaId>".
export function jobRunsOnDrama(jobs: Pick<JobRecord, 'job_id' | 'status'>[], dramaId: number): boolean {
  return jobs.some((j) => {
    if (j.status !== 'running' && j.status !== 'queued') return false
    const cut = j.job_id.lastIndexOf('_')
    return cut !== -1 && j.job_id.slice(cut + 1) === String(dramaId)
  })
}

export function lineRange(lines: Pick<ReviewLine, 'idx'>[]): string {
  if (lines.length === 0) return ''
  const a = lineNumber(lines[0].idx)
  const b = lineNumber(lines[lines.length - 1].idx)
  return a === b ? `#${a}` : `#${a}–#${b}`
}

export function chipLabel(filter: LineFilter, count: number | null, short: boolean): string {
  const n = count === null ? '' : ` ${count}`
  if (filter === 'all') return `All${n}`
  if (filter === 'flagged') return short ? `⚑${n}` : `Flagged${n}`
  return short ? `Untr.${n}` : `Untranslated${n}`
}

export function emptyMessage(filter: LineFilter, term: string): string {
  if (term) return `No lines match “${term}”.`
  if (filter === 'flagged') return 'No flagged lines. Nice.'
  if (filter === 'untranslated') return 'Every line has English.'
  return 'No lines yet. Transcribe on Source first.'
}

export function resegmentSummary(p: ResegmentPreview): string {
  const notes = `${p.notes} note${p.notes === 1 ? '' : 's'}`
  return `${p.line_count_before} → ${p.line_count_after} lines; ${p.changed.length} change; ${p.translated} translated, ${p.flagged} flagged, ${notes} would be split`
}

/** "Split 31 lines into 118; speakers re-assigned" from a re-split summary. */
export function resplitSummary(r: ResplitResult): string {
  const n = r.split_lines ?? 0
  if (n === 0) return r.note || 'No line is over the length limits. Nothing changed.'
  const pieces = (r.line_count ?? 0) - (r.lines_before ?? 0) + n
  const parts = [`Split ${n} line${n === 1 ? '' : 's'} into ${pieces}`]
  if (r.timing === 'aligned') parts.push(`${r.aligned_lines ?? 0} timed from the audio`)
  if (r.speakers_reassigned) parts.push('speakers re-assigned')
  if (r.cleared_translations) parts.push(`${r.cleared_translations} translation${r.cleared_translations === 1 ? '' : 's'} cleared`)
  return parts.join('; ') + '.' + (r.note ? ` ${r.note}` : '')
}

export const RESPLIT_SENSITIVITIES: { value: ResplitSensitivity; label: string }[] = [
  { value: 'normal', label: 'Normal' },
  { value: 'more', label: 'More' },
  { value: 'sentence', label: 'Sentence by sentence' },
]

/** Seconds offered for "Also split by duration"; null keeps the preset's own limit. */
export const RESPLIT_DURATION_CAPS = [5, 10, 15, 20]

/** "Preview: 31 lines would be split into 118." from a dry-run result. */
export function resplitPreviewSummary(r: ResplitResult): string {
  const n = r.split_lines ?? 0
  if (n === 0) return r.note || 'Preview: no line would be split.'
  const cleared = r.cleared_translations
    ? ` ${r.cleared_translations} translation${r.cleared_translations === 1 ? '' : 's'} would be cleared.`
    : ''
  return `Preview: ${n} line${n === 1 ? '' : 's'} would be split into ${r.pieces ?? 0}.${cleared}`
}

/** One line per speaker, e.g. "Anna  3:40 · 62% · 41 turns", biggest first. */
export function speakerTimeLines(s: SpeakerTimeSummary): string[] {
  return s.speakers.map((x) => `${x.label}  ${formatDuration(x.seconds)} · ${x.percent}% · ${x.turns} turn${x.turns === 1 ? '' : 's'}`)
}

/** Footer for the speaker time list: total speech and audio no turn covers. */
export function speakerTimeFooter(s: SpeakerTimeSummary): string {
  const gap = s.uncovered_seconds === null ? '' : `; ${formatDuration(s.uncovered_seconds)} of the audio has no speaker turn`
  return `${formatDuration(s.total_speech_seconds)} of speech in the saved detection${gap}.`
}

/** The server asks for confirm=true when a long line already has English. */
export function resplitNeedsConfirm(e: unknown): boolean {
  return e instanceof ApiError && e.status === 422 && /confirm/i.test(e.message)
}

// Lines someone edited while the version switch ran keep their own English.
export function keptNote(n: number): string {
  if (n <= 0) return ''
  return n === 1 ? ' 1 line was edited meanwhile and kept.' : ` ${n} lines were edited meanwhile and kept.`
}

// ---- AI re-segmentation preview (parity R47) ----

// Translation-only engines cannot suggest split points (the server refuses
// them). The engine list carries no flag for this, so the Translate stage's
// own list of such engines is reused.
export function canResegmentWith(engine: string): boolean {
  return reflectAvailable(engine)
}

export function resegmentEngines(engines: TranslateEngine[]): TranslateEngine[] {
  return engines.filter((e) => canResegmentWith(e.name))
}

/** Before starting: what running the preview costs, with this month's spend when known. */
export function resegmentCostNote(
  config: Pick<TranslateRunConfig, 'engines' | 'month_spend' | 'monthly_cap_usd' | 'cap_applies_by_engine'> | null,
  engine: string,
): string {
  const info = config?.engines.find((e) => e.name === engine)
  if (info?.free) return `${info.label} is free to run.`
  if (!config) return 'A paid AI call, counted toward the monthly spending cap.'
  const spent = spendText(config.month_spend, config.monthly_cap_usd)
  return config.cap_applies_by_engine[engine] === false
    ? `An AI call; this engine is not counted toward the monthly cap. ${spent}`
    : `A paid AI call, counted toward the monthly spending cap. ${spent}`
}

// The preview carries no cost figure; the call is logged with the drama's usage.
export const RESEGMENT_COST_RECORDED = 'The AI cost is logged with this drama’s usage (Library → Cost by drama).'

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? '' : 's'}`

/** What applying drops on the lines being split. */
export function droppedText(p: Pick<ResegmentPreview, 'translated' | 'flagged' | 'notes'>): string {
  const parts = [
    p.translated ? plural(p.translated, 'translation') : '',
    p.flagged ? plural(p.flagged, 'flag') : '',
    p.notes ? plural(p.notes, 'note') : '',
  ].filter(Boolean)
  if (parts.length === 0) return 'Lines long enough to split carry translations, flags or notes; any line that is split loses them.'
  const list = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
  return `${list} on the lines being split will be dropped.`
}

export function llmPreviewSummary(p: ResegmentPreview & { engine: string }): string {
  const n = p.changed.length
  return `${p.line_count_before} → ${p.line_count_after} lines · ${n} line${n === 1 ? '' : 's'} split · by ${humanize('engine', p.engine)}`
}

const RESEGMENT_CONFIRM_MESSAGE =
  'Lines being split now carry translations, flags or notes, which would be dropped. Type the word to apply anyway.'
export const RESEGMENT_PREVIEW_AGAIN = 'The lines changed since this preview. Preview again.'
const RESEGMENT_PREVIEW_GONE = 'This preview is no longer on the server. Preview again.'

type LlmApplyProblem = 'confirm' | 'changed' | 'gone' | 'job'

/**
 * Why applying the AI preview was refused, from the start request's error or
 * the apply job's error text; null = show the error as it is.
 */
export function llmApplyProblem(e: unknown): LlmApplyProblem | null {
  if (typeof e === 'string') {
    if (/confirm/i.test(e)) return 'confirm'
    if (/changed since the preview/i.test(e)) return 'changed'
    return null
  }
  if (!(e instanceof ApiError)) return null
  if (e.status === 422 && /confirm/i.test(e.message)) return 'confirm'
  if (e.status === 404) return 'gone'
  if (e.status === 409) return /job|already running/i.test(e.message) ? 'job' : 'changed'
  return null
}

export function llmApplyProblemText(p: LlmApplyProblem): string {
  if (p === 'confirm') return RESEGMENT_CONFIRM_MESSAGE
  if (p === 'gone') return RESEGMENT_PREVIEW_GONE
  if (p === 'job') return JOB_RUNNING_MESSAGE
  return RESEGMENT_PREVIEW_AGAIN
}
