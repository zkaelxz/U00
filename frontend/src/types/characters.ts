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
