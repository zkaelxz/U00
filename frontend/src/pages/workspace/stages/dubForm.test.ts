import { describe, expect, it } from 'vitest'

import type { DubConfig } from '../../../types/dub'
import { NARRATION_RESUME_NOTE, dubAdvancedSummary, dubSettingsLine, initialDubForm, narrationResumeNote } from './dubForm'

const cfg = {
  is_narration: false,
  narration_language: 'en',
  tts_engines: [{ key: 'edge_tts', label: 'Edge TTS', requires_internet: true }],
  defaults: { max_speedup: 1.3, max_slowdown: 0.85 },
  can_keep_background: true,
} as unknown as DubConfig

describe('dub summaries', () => {
  it('says defaults when nothing changed', () => {
    expect(dubAdvancedSummary(cfg, initialDubForm(cfg))).toBe('defaults')
  })

  it('lists only non-default values', () => {
    const form = { ...initialDubForm(cfg), maxSpeedup: 1.5, keepBackground: true }
    expect(dubAdvancedSummary(cfg, form)).toBe('speed-up 1.5x · keep background music')
  })

  it('ignores background music that the server cannot honour', () => {
    const noBgm = { ...cfg, can_keep_background: false } as DubConfig
    const form = { ...initialDubForm(noBgm), keepBackground: true }
    expect(dubAdvancedSummary(noBgm, form)).toBe('defaults')
    expect(dubSettingsLine(noBgm, form)).toBe('Edge TTS · speed 0.85x to 1.3x · no background music')
  })
})

describe('narrationResumeNote', () => {
  it('explains Start over only when a narration run resumes', () => {
    expect(narrationResumeNote('Resuming: 3 of 8 chunks already tagged...')).toBe(NARRATION_RESUME_NOTE)
    expect(narrationResumeNote('Tagging chunk 4 of 8')).toBeNull()
    expect(narrationResumeNote('Not Resuming: x')).toBeNull()
    expect(narrationResumeNote('')).toBeNull()
    expect(narrationResumeNote(null)).toBeNull()
    expect(narrationResumeNote(undefined)).toBeNull()
  })
})
