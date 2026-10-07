import { describe, expect, it } from 'vitest'

import type { DubConfig } from '../../../types/dub'
import {
  NARRATION_RESUME_NOTE,
  dubAdvancedSummary,
  dubBlocker,
  dubSettingsLine,
  initialDubForm,
  narrationResumeNote,
  untranslatedNarrationWarning,
} from './dubForm'

const cfg = {
  is_narration: false,
  narration_language: 'en',
  tts_engines: [
    { key: 'chatterbox', label: 'Chatterbox' },
    { key: 'omnivoice', label: 'OmniVoice' },
  ],
  default_engine: 'omnivoice',
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
    expect(dubSettingsLine(noBgm, form)).toBe('OmniVoice · speed 0.85x to 1.3x · no background music')
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

describe('untranslated narration warning (U01)', () => {
  it('says the English narration will be silent, singular and plural', () => {
    expect(untranslatedNarrationWarning('translation', 1)).toBe(
      '1 line has no English yet and will be silent in the narration. Translate it first.',
    )
    expect(untranslatedNarrationWarning('translation', 4)).toBe(
      '4 lines have no English yet and will be silent in the narration. Translate them first.',
    )
  })

  it('says source-language narration still generates but subtitles lose the English half', () => {
    expect(untranslatedNarrationWarning('original', 1)).toBe(
      '1 line has no translation yet. Narration will still generate for it (it speaks the source text), ' +
        'but its exported subtitles will be missing the English half of the bilingual pair. ' +
        'Translate first if you want complete subtitles.',
    )
    expect(untranslatedNarrationWarning('original', 3)).toBe(
      '3 lines have no translation yet. Narration will still generate for them (it speaks the source text), ' +
        'but their exported subtitles will be missing the English half of the bilingual pair. ' +
        'Translate first if you want complete subtitles.',
    )
  })

  it('is null when nothing is missing, the count is unknown, or the language is unknown', () => {
    expect(untranslatedNarrationWarning('translation', 0)).toBeNull()
    expect(untranslatedNarrationWarning('original', 0)).toBeNull()
    expect(untranslatedNarrationWarning('translation', null)).toBeNull()
    expect(untranslatedNarrationWarning('original', undefined)).toBeNull()
    expect(untranslatedNarrationWarning('translation', Number.NaN)).toBeNull()
    expect(untranslatedNarrationWarning('en', 2)).toBeNull()
  })
})

describe('dubBlocker', () => {
  const ready = { ...cfg, speakable_line_count: 3 } as DubConfig
  it('is null when the chosen engine can run', () => {
    expect(dubBlocker(ready, initialDubForm(ready))).toBeNull()
  })
  it("gives the engine's own missing-package reason", () => {
    const missing = {
      ...ready,
      tts_engines: [{ key: 'omnivoice', label: 'OmniVoice', unavailable_reason: 'OmniVoice is not installed. Install it in Diagnostics.' }],
    } as DubConfig
    expect(dubBlocker(missing, initialDubForm(missing))).toBe('OmniVoice is not installed. Install it in Diagnostics.')
  })
  it('starts on the default engine, not the first one listed', () => {
    expect(initialDubForm(cfg).engine).toBe('omnivoice')
  })
  it('shows the server blocker (no engine installed, or a removed engine) before anything else', () => {
    const none = { ...ready, blocker: 'No voice engine is installed. Install one in Diagnostics.' } as DubConfig
    expect(dubBlocker(none, initialDubForm(none))).toBe('No voice engine is installed. Install one in Diagnostics.')
    const removed = { ...ready, blocker: 'Lin: The F5-TTS engine was removed. Pick another voice engine in Dub.' } as DubConfig
    expect(dubBlocker(removed, initialDubForm(removed))).toContain('The F5-TTS engine was removed.')
  })
  it('keeps the no-text blocker first', () => {
    const empty = { ...cfg, speakable_line_count: 0, blocker: 'No voice engine is installed. Install one in Diagnostics.' } as DubConfig
    expect(dubBlocker(empty, initialDubForm(empty))).toBe('There is no text to speak yet.')
  })
})
