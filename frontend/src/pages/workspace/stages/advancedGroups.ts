/*
 * The Tools tab's advanced options, grouped by purpose. The ids name the controls
 * TranscribeAdvanced renders; this table decides which fold each one is in and which
 * ones need Developer Mode, so a control cannot be lost or doubled by a layout edit.
 */
export type AdvancedFieldId =
  | 'expected_speakers' | 'min_speakers' | 'max_speakers' | 'detect_speakers_only'
  | 'sensitivity_preset' | 'min_silence_ms' | 'min_pause_sec' | 'vad_threshold' | 'hardsub_interval_sec'
  | 'alignment_method' | 'asr_backend_choice' | 'voice_detector' | 'prompt'
  | 'beam_size' | 'separation_backend' | 'hardsub_ocr_backend'
  | 'hallucination_silence_sec'
  | 'separate_vocals_first' | 'realign_long_segments' | 'whisper_fast_mode' | 'split_by_sentences'
  | 'vocabulary_hint' | 'whisper_repeat_guard' | 'use_groq'

export interface AdvancedGroup {
  id: 'speakers' | 'timing' | 'models' | 'guards' | 'switches'
  label: string
  storageKey: string
  defaultOpen: boolean
  fields: AdvancedFieldId[]
}

const GROUPS: Omit<AdvancedGroup, 'storageKey' | 'defaultOpen'>[] = [
  { id: 'speakers', label: 'Speakers', fields: ['expected_speakers', 'min_speakers', 'max_speakers', 'detect_speakers_only'] },
  { id: 'timing', label: 'Timing', fields: ['sensitivity_preset', 'min_silence_ms', 'min_pause_sec', 'vad_threshold', 'hardsub_interval_sec'] },
  {
    id: 'models',
    label: 'Models and backends',
    fields: ['alignment_method', 'asr_backend_choice', 'voice_detector', 'prompt', 'beam_size', 'separation_backend', 'hardsub_ocr_backend'],
  },
  { id: 'guards', label: 'Guards', fields: ['hallucination_silence_sec'] },
  // Every on/off option in one card, so the list can fold away once it is set.
  {
    id: 'switches',
    label: 'Switches',
    fields: ['separate_vocals_first', 'realign_long_segments', 'whisper_fast_mode', 'split_by_sentences', 'vocabulary_hint', 'whisper_repeat_guard', 'use_groq'],
  },
]

export const DEVELOPER_ONLY_FIELDS: readonly AdvancedFieldId[] = [
  'vad_threshold', 'hardsub_interval_sec', 'beam_size', 'separation_backend', 'hardsub_ocr_backend',
  'hallucination_silence_sec', 'whisper_repeat_guard',
]

export const ADVANCED_FIELD_IDS: readonly AdvancedFieldId[] = GROUPS.flatMap((g) => g.fields)

/**
 * The groups to show, without Developer Mode controls unless it is on, and without any group left empty.
 * Exactly one fold opens by itself the first time: Switches while the title's options have never been
 * saved (that is what a first visit sets), Speakers once they have. What the viewer opens or closes after
 * that is remembered by the fold and wins over these.
 */
export function advancedGroups(developerMode: boolean, optionsSaved = false): AdvancedGroup[] {
  return GROUPS.map((g) => ({
    ...g,
    storageKey: `source.advanced.${g.id}`,
    defaultOpen: g.id === (optionsSaved ? 'speakers' : 'switches'),
    fields: developerMode ? g.fields : g.fields.filter((f) => !DEVELOPER_ONLY_FIELDS.includes(f)),
  })).filter((g) => g.fields.length > 0)
}
