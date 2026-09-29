import type { LinePatch, ReviewLine, ReviewMatch } from '../../../../types/review'

export interface LineDraft {
  zh: string
  en: string
  speaker: string
  start: string
  end: string
}

export const PAGE_SIZE = 40

export function draftFromLine(line: ReviewLine): LineDraft {
  return {
    zh: line.zh,
    en: line.en,
    speaker: line.speaker ?? '',
    start: String(line.start),
    end: String(line.end),
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

export function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  return `${m}:${(seconds - m * 60).toFixed(2).padStart(5, '0')}`
}

export function pageCount(total: number, pageSize: number = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / pageSize))
}

export const CONFLICT_MESSAGE = 'This line changed elsewhere. Reload and try again.'

// Stale ids from an apply, shown as the line numbers the user sees.
export function staleLabels(staleIds: number[], matches: ReviewMatch[]): string[] {
  return staleIds.map((id) => {
    const m = matches.find((x) => x.id === id)
    return m ? `#${m.idx}` : `line id ${id}`
  })
}
