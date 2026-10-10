import { describe, expect, it } from 'vitest'

import type { CharacterEntry } from '../../../types/translateStage'
import { ApiError } from '../../../api/client'
import { VOICE_CLIP_NOTE, leavesVoiceClip, mergeChoices, mergeSummary, readMergeUndo, saveMergeUndo, undoIdSurvives } from './mergeSpeakers'

const e = (label: string, over: Partial<CharacterEntry> = {}) =>
  ({ speaker_label: label, character_name: '', line_count: 2, series_character_id: null, ...over }) as CharacterEntry

describe('mergeChoices', () => {
  it('lists the other speakers with name and line count', () => {
    const rows = [e('A'), e('B', { character_name: 'Mei', line_count: 1 }), e('C')]
    expect(mergeChoices(rows, rows[0]).map((c) => c.text)).toEqual(['B (Mei), 1 line', 'C, 2 lines'])
  })

  it('blocks a speaker linked to a different series character only', () => {
    const rows = [e('A', { series_character_id: 1 }), e('B', { series_character_id: 2 }), e('C', { series_character_id: 1 }), e('D')]
    expect(mergeChoices(rows, rows[0]).map((c) => c.blocked !== null)).toEqual([true, false, false])
  })

  it('is empty with one speaker', () => {
    expect(mergeChoices([e('A')], e('A'))).toEqual([])
  })
})

describe('mergeSummary', () => {
  it('says how many lines move', () => {
    expect(mergeSummary(e('B', { line_count: 12 }), 'A')).toMatch(/^12 lines from B will move to A/)
    expect(mergeSummary(e('B', { line_count: 1 }), 'A')).toMatch(/^1 line from B/)
  })
})

describe('leavesVoiceClip', () => {
  const src = e('B', { has_ref_audio: true })
  const tgt = (over: Partial<CharacterEntry> = {}) => e('A', { has_ref_audio: false, voice_design: '', ...over })

  it('is false when the target takes the clip', () => {
    expect(leavesVoiceClip(src, tgt())).toBe(false)
  })
  it('is true when the target has its own clip or a designed voice', () => {
    expect(leavesVoiceClip(src, tgt({ has_ref_audio: true }))).toBe(true)
    expect(leavesVoiceClip(src, tgt({ voice_design: 'warm, low' }))).toBe(true)
  })
  it('is false when the source has no clip or the target is unknown', () => {
    expect(leavesVoiceClip(e('B', { has_ref_audio: false }), tgt({ has_ref_audio: true }))).toBe(false)
    expect(leavesVoiceClip(src, undefined)).toBe(false)
  })
  it('has the plain wording', () => {
    expect(VOICE_CLIP_NOTE).toBe('Its voice sample stays on disk. You can remove it later in Library tools > Disk usage.')
  })
})

describe('saved merge undo', () => {
  const handle = { id: 'opaque', source: 'B', target: 'A', expiresAt: 5000 }
  const store = () => {
    const m = new Map<string, string>()
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) }
  }

  it('round-trips the handle and forgets it once expired or cleared', () => {
    const s = store()
    saveMergeUndo(1, handle, s)
    expect(readMergeUndo(1, 1000, s)).toEqual(handle)
    expect(readMergeUndo(2, 1000, s)).toBeNull()
    expect(readMergeUndo(1, 5000, s)).toBeNull()
    saveMergeUndo(1, null, s)
    expect(readMergeUndo(1, 1000, s)).toBeNull()
  })

  it('ignores anything that is not a handle', () => {
    const s = store()
    s.setItem('baihe.characters.mergeUndo.1', JSON.stringify({ source_row: {}, previous: [] }))
    expect(readMergeUndo(1, 0, s)).toBeNull()
  })
})

describe('undoIdSurvives', () => {
  const err = (status: number, details?: unknown) => new ApiError(status, { code: 'x', message: 'm', details })

  it('keeps the id on a job-running 409 and on a network failure', () => {
    expect(undoIdSurvives(err(409, { reason: 'job_running' }))).toBe(true)
    expect(undoIdSurvives(new TypeError('Failed to fetch'))).toBe(true)
    expect(undoIdSurvives(err(0))).toBe(true)
  })

  it('forgets it on not found and on a stale 409', () => {
    expect(undoIdSurvives(err(404))).toBe(false)
    expect(undoIdSurvives(err(409))).toBe(false)
  })
})
