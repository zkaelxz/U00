import type { LinePatch, ReviewLine } from '../../../../types/review'
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

// A draft that would send something (or is invalid) is dirty: navigation saves it first.
export function isDirty(line: ReviewLine, draft: LineDraft): boolean {
  return buildPatch(line, draft) !== null
}

export const CONFLICT_MESSAGE = 'This line changed elsewhere. Reload and try again.'

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
