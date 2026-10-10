import { describe, expect, it } from 'vitest'

import type { SubtitleImportPreview } from '../../../types/subtitleImport'
import {
  checkSubtitleFile,
  confirmLabel,
  fileSummary,
  impactLine,
  MAX_SUBTITLE_BYTES,
  needsConfirm,
} from './subtitleImportModel'

const preview = (over: Partial<SubtitleImportPreview> = {}): SubtitleImportPreview => ({
  format: 'srt', encoding: 'utf-8', encoding_guessed: false, cue_count: 10, duration_seconds: 60,
  detected_language: null, bilingual_suspected: false, problems: [], blocking: false, sample: [],
  mode: 'source', existing_line_count: 0, replaces_lines: 0, matched_lines: 0, unmatched_cues: 0,
  overwrites: 0, unsplit_cues: 0, blocked_reason: null, ...over,
})

describe('checkSubtitleFile', () => {
  it('accepts the five extensions in any case and refuses others, empty and oversized files', () => {
    for (const n of ['a.srt', 'a.VTT', 'a.ass', 'a.ssa', 'a.Lrc']) expect(checkSubtitleFile(n, 10)).toBeNull()
    expect(checkSubtitleFile('a.txt', 10)).toMatch(/SRT, VTT, ASS, SSA or LRC/)
    expect(checkSubtitleFile('noext', 10)).toMatch(/SRT/)
    expect(checkSubtitleFile('a.srt', 0)).toBe('That file is empty.')
    expect(checkSubtitleFile('a.srt', MAX_SUBTITLE_BYTES + 1)).toMatch(/too large/)
  })
})

describe('preview wording', () => {
  it('summarises format, cues, encoding and language', () => {
    expect(fileSummary(preview())).toBe('SRT · 10 cues · read as utf-8')
    expect(fileSummary(preview({ cue_count: 1, encoding: 'cp932', encoding_guessed: true, detected_language: 'ja' })))
      .toBe('SRT · 1 cue · read as cp932 (guessed) · Japanese')
  })

  it('says what a source import replaces or adds', () => {
    expect(impactLine(preview())).toBe("Adds 10 lines, with the file's times.")
    expect(impactLine(preview({ replaces_lines: 1 }))).toBe("Replaces this title's 1 line with 10 from the file.")
  })

  it('says how a translation import matches, and passes a blocked reason through', () => {
    expect(impactLine(preview({ mode: 'translation', matched_lines: 4, existing_line_count: 5, unmatched_cues: 1 })))
      .toBe('Puts text on 4 of 5 lines, matched by time. 1 cue matches no line.')
    expect(impactLine(preview({ mode: 'translation', blocked_reason: 'This title has no lines yet.' })))
      .toBe('This title has no lines yet.')
  })

  it('asks for the confirmation that matches the mode', () => {
    expect(needsConfirm(preview())).toBeNull()
    expect(needsConfirm(preview({ replaces_lines: 2 }))).toBe('replace')
    expect(needsConfirm(preview({ mode: 'translation', replaces_lines: 2 }))).toBeNull()
    expect(needsConfirm(preview({ mode: 'translation', overwrites: 3 }))).toBe('overwrite')
    expect(confirmLabel(preview({ replaces_lines: 5 }))).toBe('Replace the 5 current lines (saved to history first)')
    expect(confirmLabel(preview({ mode: 'translation', overwrites: 1 }))).toBe('Overwrite 1 existing translation (saved to history first)')
  })
})
