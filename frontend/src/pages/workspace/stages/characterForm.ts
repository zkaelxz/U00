import type { CharacterEntry, CharacterUpdate } from '../../../types/translateStage'

export interface CharacterForm {
  character_name: string
  pronouns: string
  tts_voice: string
  offline_voice: string
  clone_engine: string
  voice_design: string
  /** Write-only on the API: blank means "leave the stored text alone". */
  ref_text: string
}

export const toCharacterForm = (e: CharacterEntry): CharacterForm => ({
  character_name: e.character_name,
  pronouns: e.pronouns,
  tts_voice: e.tts_voice,
  offline_voice: e.offline_voice,
  clone_engine: e.clone_engine,
  voice_design: e.voice_design,
  ref_text: '',
})

const EDITABLE = ['pronouns', 'tts_voice', 'offline_voice', 'clone_engine', 'voice_design'] as const

/** Only changed fields are sent (omitted = leave alone, "" = clear). */
export function buildCharacterUpdate(entry: CharacterEntry, f: CharacterForm): CharacterUpdate {
  const u: CharacterUpdate = { speaker_label: entry.speaker_label }
  if (f.character_name !== entry.character_name) u.character_name = f.character_name.trim()
  for (const k of EDITABLE) if (f[k] !== entry[k]) u[k] = f[k]
  if (f.ref_text.trim()) u.ref_text = f.ref_text.trim()
  return u
}

export const isDirty = (u: CharacterUpdate) => Object.keys(u).length > 1
