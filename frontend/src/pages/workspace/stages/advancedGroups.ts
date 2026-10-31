/*
 * The Tools tab's advanced options, grouped by purpose. The ids name the controls
 * TranscribeAdvanced renders; this table decides which fold each one is in and which
 * ones need Developer Mode, so a control cannot be lost or doubled by a layout edit.
 */
import { appliesTo, type SettingGroup } from '../mediaTypeSettings'
import type { TitleKind } from '../titleKind'

export type AdvancedFieldId =
  | 'expected_speakers' | 'min_speakers' | 'max_speakers' | 'detect_speakers_only'
  | 'sensitivity_preset' | 'min_silence_ms' | 'min_pause_sec' | 'vad_threshold' | 'hardsub_interval_sec'
  | 'alignment_method' | 'asr_backend_choice' | 'voice_detector' | 'prompt'
  | 'beam_size' | 'separation_backend' | 'hardsub_ocr_backend'
  | 'hallucination_silence_sec'
  | 'separate_vocals_first' | 'realign_long_segments' | 'whisper_fast_mode' | 'split_by_sentences'
  | 'difficult_audio' | 'vocabulary_hint' | 'whisper_repeat_guard' | 'use_groq'

interface AdvancedGroup {
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
    fields: ['separate_vocals_first', 'realign_long_segments', 'whisper_fast_mode', 'split_by_sentences', 'difficult_audio', 'vocabulary_hint', 'whisper_repeat_guard', 'use_groq'],
  },
]

export const DEVELOPER_ONLY_FIELDS: readonly AdvancedFieldId[] = [
  'vad_threshold', 'hardsub_interval_sec', 'beam_size', 'separation_backend', 'hardsub_ocr_backend',
  'hallucination_silence_sec', 'whisper_repeat_guard',
]

// Which kind of setting each control is, so a title that lacks that kind never renders it.
const FIELD_GROUP: Record<AdvancedFieldId, SettingGroup> = {
  expected_speakers: 'speakers', min_speakers: 'speakers', max_speakers: 'speakers', detect_speakers_only: 'speakers',
  sensitivity_preset: 'speech_tuning', min_silence_ms: 'speech_tuning', min_pause_sec: 'speech_tuning', vad_threshold: 'speech_tuning',
  hardsub_interval_sec: 'hardsub', hardsub_ocr_backend: 'hardsub',
  alignment_method: 'speech_tuning', asr_backend_choice: 'speech_tuning', voice_detector: 'speech_tuning', prompt: 'speech_tuning',
  beam_size: 'speech_tuning', hallucination_silence_sec: 'speech_tuning', realign_long_segments: 'speech_tuning',
  whisper_fast_mode: 'speech_tuning', split_by_sentences: 'speech_tuning', difficult_audio: 'speech_tuning', vocabulary_hint: 'speech_tuning',
  whisper_repeat_guard: 'speech_tuning', use_groq: 'speech_tuning',
  separation_backend: 'audio_prep', separate_vocals_first: 'audio_prep',
}

export const ADVANCED_FIELD_IDS: readonly AdvancedFieldId[] = GROUPS.flatMap((g) => g.fields)

/**
 * The groups to show, without Developer Mode controls unless it is on, without controls the title's kind
 * does not have, and without any group left empty.
 * Exactly one fold opens by itself the first time: Switches while the title's options have never been
 * saved (that is what a first visit sets), Speakers once they have. What the viewer opens or closes after
 * that is remembered by the fold and wins over these.
 */
export function advancedGroups(developerMode: boolean, optionsSaved = false, kind: TitleKind = 'subtitle'): AdvancedGroup[] {
  return GROUPS.map((g) => ({
    ...g,
    storageKey: `source.advanced.${g.id}`,
    defaultOpen: g.id === (optionsSaved ? 'speakers' : 'switches'),
    fields: g.fields.filter((f) => appliesTo(kind, FIELD_GROUP[f]) && (developerMode || !DEVELOPER_ONLY_FIELDS.includes(f))),
  })).filter((g) => g.fields.length > 0)
}
