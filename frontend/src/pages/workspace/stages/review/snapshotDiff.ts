import type { HistorySnapshotLine, ReviewLine } from '../../../../types/review'
import { lineNumber } from '../../../../lineNumber'

export const PREVIEW_CAP = 5
const TEXT_CAP = 60

export interface PreviewChange {
  number: number
  field: string
  before: string
  after: string
}

export interface SnapshotPreview {
  changed: number
  added: number
  removed: number
  shown: PreviewChange[]
  more: number
}

const clip = (s: string) => (s.length > TEXT_CAP ? `${s.slice(0, TEXT_CAP)}…` : s)
const time = (l: { start: number | null; end: number | null }) => `${l.start ?? '?'}–${l.end ?? '?'}s`

// Restore matches lines by id (a snapshot line whose id is gone comes back as
// a new line), so the preview does the same rather than comparing positions.
export function previewSnapshot(current: ReviewLine[], snapshot: HistorySnapshotLine[]): SnapshotPreview {
  const byId = new Map(current.map((l) => [l.id, l]))
  const snapIds = new Set(snapshot.map((l) => l.id))
  const changes: PreviewChange[] = []
  let added = 0
  for (const s of snapshot) {
    const cur = s.id == null ? undefined : byId.get(s.id)
    if (!cur) { added += 1; continue }
    const number = lineNumber(cur.idx)
    if (cur.en !== s.en) changes.push({ number, field: 'Translation', before: cur.en, after: s.en })
    else if (cur.zh !== s.zh) changes.push({ number, field: 'Source', before: cur.zh, after: s.zh })
    else if ((cur.speaker ?? '') !== (s.speaker ?? '')) {
      changes.push({ number, field: 'Speaker', before: cur.speaker ?? '', after: s.speaker ?? '' })
    } else if (cur.start !== s.start || cur.end !== s.end) {
      changes.push({ number, field: 'Timing', before: time(cur), after: time(s) })
    }
  }
  const removed = current.filter((l) => !snapIds.has(l.id)).length
  return {
    changed: changes.length,
    added,
    removed,
    shown: changes.slice(0, PREVIEW_CAP).map((c) => ({ ...c, before: clip(c.before), after: clip(c.after) })),
    more: Math.max(0, changes.length - PREVIEW_CAP),
  }
}

export function previewSummary(p: SnapshotPreview): string {
  const total = p.changed + p.added + p.removed
  if (total === 0) return 'No lines would change.'
  const parts = [`${p.changed} edited`, `${p.added} added`, `${p.removed} removed`]
  return `${total} line${total === 1 ? '' : 's'} would change (${parts.join(', ')}).`
}
