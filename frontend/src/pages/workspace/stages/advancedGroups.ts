/*
 * The Tools tab's advanced options, grouped by purpose. The ids name the controls
 * TranscribeAdvanced renders; this table decides which fold each one is in and which
 * ones need Developer Mode, so a control cannot be lost or doubled by a layout edit.
 */
export type AdvancedFieldId =
  | 'expected_speakers' | 'min_speakers' | 'max_speakers' | 'detect_speakers_only'
  | 'sensitivity_preset' | 'min_silence_ms' | 'min_pause_sec' | 'split_by_sentences' | 'realign_long_segments'
  | 'vad_threshold' | 'hardsub_interval_sec'
  | 'alignment_method' | 'asr_backend_choice' | 'voice_detector' | 'use_groq' | 'whisper_fast_mode'
  | 'vocabulary_hint' | 'extra_names' | 'replacement_prompt'
  | 'beam_size' | 'separation_backend' | 'hardsub_ocr_backend'
  | 'separate_vocals_first' | 'hallucination_silence_sec' | 'whisper_repeat_guard'

export interface AdvancedGroup {
  id: 'speakers' | 'timing' | 'models' | 'guards'
  label: string
  storageKey: string
  defaultOpen: boolean
  fields: AdvancedFieldId[]
}

const GROUPS: Omit<AdvancedGroup, 'storageKey' | 'defaultOpen'>[] = [
  { id: 'speakers', label: 'Speakers', fields: ['expected_speakers', 'min_speakers', 'max_speakers', 'detect_speakers_only'] },
  {
    id: 'timing',
    label: 'Timing',
    fields: ['sensitivity_preset', 'min_silence_ms', 'min_pause_sec', 'split_by_sentences', 'realign_long_segments', 'vad_threshold', 'hardsub_interval_sec'],
  },
  {
    id: 'models',
    label: 'Models and backends',
    fields: [
      'alignment_method', 'asr_backend_choice', 'voice_detector', 'use_groq', 'whisper_fast_mode', 'vocabulary_hint',
      'extra_names', 'replacement_prompt', 'beam_size', 'separation_backend', 'hardsub_ocr_backend',
    ],
  },
  { id: 'guards', label: 'Guards', fields: ['separate_vocals_first', 'hallucination_silence_sec', 'whisper_repeat_guard'] },
]

export const DEVELOPER_ONLY_FIELDS: readonly AdvancedFieldId[] = [
  'vad_threshold', 'hardsub_interval_sec', 'beam_size', 'separation_backend', 'hardsub_ocr_backend',
  'hallucination_silence_sec', 'whisper_repeat_guard',
]

export const ADVANCED_FIELD_IDS: readonly AdvancedFieldId[] = GROUPS.flatMap((g) => g.fields)

/** The groups to show, without Developer Mode controls unless it is on, and without any group left empty. */
export function advancedGroups(developerMode: boolean): AdvancedGroup[] {
  return GROUPS.map((g, i) => ({
    ...g,
    storageKey: `source.advanced.${g.id}`,
    // Only the first fold opens by itself; the viewer's choice is remembered after that.
    defaultOpen: i === 0,
    fields: developerMode ? g.fields : g.fields.filter((f) => !DEVELOPER_ONLY_FIELDS.includes(f)),
  })).filter((g) => g.fields.length > 0)
}
