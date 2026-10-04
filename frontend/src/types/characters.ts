import type { SeriesCharacter } from './autotuneGlossary'
import type { CharacterEntry } from './translateStage'

// Inventory C02: an experimental "this speaker sounds like X" match.
export interface VoiceSuggestion {
  speaker_label: string
  series_character_id: number
  character_name: string
  similarity: number
}

/** Accept returns the renamed speaker; reject returns character: null. */
export interface VoiceSuggestionResult {
  character: CharacterEntry | null
  suggestions: VoiceSuggestion[]
}

// Inventory C08: "Remember as a known series character".
export interface RememberResult {
  character: CharacterEntry
  series_character: SeriesCharacter
  created: boolean
}

// Rename a speaker once: every line and the Characters row; undo is what
// the server needs to put the old labels back.
export interface RenameUndo {
  speaker_label: string
  previous_label: string
  previous_character_name: string | null
  previous: { id: number; speaker: string; speaker_manual: boolean }[]
}

export interface RenameResult {
  characters: CharacterEntry[]
  renamed: number
  undo: RenameUndo | null
}

// Merge two speakers: the source's lines and Characters row move into the
// target. The undo carries both rows as they were; the server re-validates it.
export interface MergeRow {
  character_name: string | null
  voice_actor: string | null
  tts_voice: string | null
  offline_voice: string | null
  ref_audio_filename: string | null
  ref_text: string | null
  elevenlabs_voice_id: string | null
  clone_engine: string | null
  voice_design: string | null
  pronouns: string | null
  series_character_id: number | null
}

export interface MergeUndo {
  source_label: string
  target_label: string
  source_row: MergeRow
  target_row: MergeRow | null
  previous: { id: number; speaker: string; speaker_manual: boolean }[]
}

export interface MergeResult {
  characters: CharacterEntry[]
  moved: number
  undo: MergeUndo | null
}
