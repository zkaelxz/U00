import { describe, expect, it } from 'vitest'

import { pickDraft, readDraft, writeDraft } from '../../../../hooks/useStageDraft'
import { MERGE_DRAFT_STAGE, mergeFormDefaults } from './aiExtrasLogic'
import { COMPARE_DRAFT_SHAPE, COMPARE_DRAFT_STAGE } from './compareTranscriptionLogic'
import { RESPLIT_DRAFT_SHAPE, RESPLIT_DRAFT_STAGE } from './reviewResegment'

// The Review tools' drafts: what each form writes, read back through its shape.
const memory = () => {
  const m = new Map<string, string>()
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) }
}

describe('review tool drafts', () => {
  it('Compare transcription keeps its run options and drops wrong types', () => {
    const s = memory()
    const values = { size: 'medium', backend: 'qwen3_asr', translate: true, retranslate: false, extraNames: '沈清疑', hint: '' }
    writeDraft(s, 1, COMPARE_DRAFT_STAGE, values)
    expect(pickDraft(readDraft(s, 1, COMPARE_DRAFT_STAGE), COMPARE_DRAFT_SHAPE)).toEqual(values)
    expect(pickDraft({ size: 3, translate: 'yes', hint: 'names' }, COMPARE_DRAFT_SHAPE)).toEqual({ hint: 'names' })
  })

  it('Re-split keeps align, sensitivity and the duration cap (0 = preset default)', () => {
    const s = memory()
    writeDraft(s, 1, RESPLIT_DRAFT_STAGE, { align: true, sensitivity: 'more', capSec: 10 })
    expect(pickDraft(readDraft(s, 1, RESPLIT_DRAFT_STAGE), RESPLIT_DRAFT_SHAPE)).toEqual({ align: true, sensitivity: 'more', capSec: 10 })
    expect(pickDraft({ align: 'on', capSec: '10' }, RESPLIT_DRAFT_SHAPE)).toEqual({})
  })

  it('Merge short lines keeps its three thresholds as typed', () => {
    const s = memory()
    const form = { ...mergeFormDefaults(), max_gap: '0.9' }
    writeDraft(s, 1, MERGE_DRAFT_STAGE, form)
    expect(pickDraft(readDraft(s, 1, MERGE_DRAFT_STAGE), mergeFormDefaults())).toEqual(form)
    expect(pickDraft(readDraft(s, 2, MERGE_DRAFT_STAGE), mergeFormDefaults())).toEqual({})
    expect(pickDraft({ max_gap: 0.9 }, mergeFormDefaults())).toEqual({})
  })

  it('each tool has its own key', () => {
    expect(new Set([COMPARE_DRAFT_STAGE, RESPLIT_DRAFT_STAGE, MERGE_DRAFT_STAGE]).size).toBe(3)
  })
})
