import type { LinePatch, ReviewLine } from '../../../../types/review'
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
