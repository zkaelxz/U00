import type { SourceChangedRunBody, TranslateRunConfig } from '../../../types/translateStage'

// The review flag a translated line gets when its source text changes (source_change.py).
export const SOURCE_CHANGED_FLAG = 'source_changed'

export const isSourceChanged = (line: { flag: string | null }) => line.flag === SOURCE_CHANGED_FLAG

export function sourceChangedText(n: number): string {
  return n === 1
    ? '1 line changed in the source since it was translated'
    : `${n} lines changed in the source since they were translated`
}

// "Retranslate these" runs with what the Translate form starts from: the title's
// engine and saved toggles, the Settings defaults and the stage's batch sizes.
export function retranslateBody(c: TranslateRunConfig, lineIds: number[]): SourceChangedRunBody {
  return {
    line_ids: lineIds,
    engine: c.translation_engine,
    style_preset: c.default_style_preset,
    style_note: c.default_style_note ?? '',
    locale: c.default_locale || 'en-US',
    context_window: c.defaults.context_window,
    context_window_ahead: c.defaults.context_window_ahead,
    batch_size: c.defaults.batch_size,
    ...(typeof c.default_female_pronouns === 'boolean' ? { default_female_pronouns: c.default_female_pronouns } : {}),
    ...(typeof c.include_genre_notes === 'boolean' ? { include_genre_notes: c.include_genre_notes } : {}),
  }
}
