import { describe, expect, it } from 'vitest'

import type { HistorySnapshotLine, ReviewLine } from '../../../../types/review'
import { PREVIEW_CAP, previewSnapshot, previewSummary } from './snapshotPreview'

const cur = (id: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx: id - 1, start: id, end: id + 1, zh: `z${id}`, en: `e${id}`, speaker: null,
  speaker_manual: false, sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, ...over,
})
const snap = (id: number | null, over: Partial<HistorySnapshotLine> = {}): HistorySnapshotLine => ({
  id, idx: (id ?? 9) - 1, start: id, end: (id ?? 9) + 1, zh: `z${id}`, en: `e${id}`, speaker: null,
  speaker_manual: false, dub_filename: null, ...over,
})

describe('previewSnapshot', () => {
  it('reports nothing for identical lines', () => {
    const p = previewSnapshot([cur(1), cur(2)], [snap(1), snap(2)])
    expect(previewSummary(p)).toBe('No lines would change.')
  })

  it('matches by id, not position', () => {
    const p = previewSnapshot([cur(1), cur(2)], [snap(2), snap(1)])
    expect(p.changed).toBe(0)
  })

  it('counts edited, added and removed lines', () => {
    const p = previewSnapshot([cur(1), cur(2)], [snap(1, { en: 'old' }), snap(7)])
    expect(p).toMatchObject({ changed: 1, added: 1, removed: 1 })
    expect(p.shown[0]).toEqual({ number: 1, field: 'Translation', before: 'e1', after: 'old' })
    expect(previewSummary(p)).toBe('3 lines would change (1 edited, 1 added, 1 removed).')
  })

  it('caps the list and reports the rest', () => {
    const ids = [1, 2, 3, 4, 5, 6, 7]
    const p = previewSnapshot(ids.map((i) => cur(i)), ids.map((i) => snap(i, { en: 'x' })))
    expect(p.shown).toHaveLength(PREVIEW_CAP)
    expect(p.more).toBe(2)
  })
})
