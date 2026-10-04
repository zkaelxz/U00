import type { CharacterEntry, CharacterUpdate } from '../../../types/translateStage'
import { CUSTOM, PRONOUN_PRESETS, normalizePronouns } from './seriesPeopleForm'

export { CUSTOM, PRONOUN_PRESETS }

/** Server-side cap (characters_service.MAX_PRONOUNS_LEN). */
export const MAX_PRONOUNS_LEN = 40

export interface CharacterForm {
  character_name: string
  /** '' (unspecified / series default), a preset, or CUSTOM. */
  pronoun_choice: string
  custom_pronouns: string
  tts_voice: string
  offline_voice: string
  clone_engine: string
  voice_design: string
  /** Write-only on the API: blank means "leave the stored text alone". */
  ref_text: string
}

const isPreset = (p: string) => p === '' || (PRONOUN_PRESETS as readonly string[]).includes(p)

export function toCharacterForm(e: CharacterEntry): CharacterForm {
  const p = normalizePronouns(e.pronouns)
  return {
    character_name: e.character_name,
    pronoun_choice: isPreset(p) ? p : CUSTOM,
    custom_pronouns: isPreset(p) ? '' : p,
    tts_voice: e.tts_voice,
    offline_voice: e.offline_voice,
    clone_engine: e.clone_engine,
    voice_design: e.voice_design,
    ref_text: '',
  }
}

/** The pronoun text the form stands for. Custom with nothing typed keeps
 * `fallback` (the saved value). */
export function formPronouns(f: CharacterForm, fallback = ''): string {
  if (f.pronoun_choice !== CUSTOM) return f.pronoun_choice
  return f.custom_pronouns.trim() || fallback
}

const EDITABLE = ['tts_voice', 'offline_voice', 'clone_engine', 'voice_design'] as const

/** Only changed fields are sent (omitted = leave alone, "" = clear). */
export function buildCharacterUpdate(entry: CharacterEntry, f: CharacterForm): CharacterUpdate {
  const u: CharacterUpdate = { speaker_label: entry.speaker_label }
  if (f.character_name !== entry.character_name) u.character_name = f.character_name.trim()
  const current = normalizePronouns(entry.pronouns)
  const pronouns = formPronouns(f, current)
  if (pronouns !== current) u.pronouns = pronouns
  for (const k of EDITABLE) if (f[k] !== entry[k]) u[k] = f[k]
  if (f.ref_text.trim()) u.ref_text = f.ref_text.trim()
  return u
}

export const isDirty = (u: CharacterUpdate) => Object.keys(u).length > 1

/** Label of the "no pronouns set for this drama" option: the linked
 * series character's default when there is one (shown, never copied). */
export function unsetPronounsLabel(e: CharacterEntry): string {
  const d = normalizePronouns(e.series_pronouns ?? '')
  return d ? `Series default (${d})` : 'Unspecified'
}

/** Sample lines caption (C04), or null when the server sent none. */
export function sampleCaption(e: CharacterEntry): string | null {
  if (e.sample_lines === undefined) return null
  return e.sample_lines.length ? e.sample_lines.map((s) => `“${s}”`).join(' / ') : 'No lines attributed to this speaker yet.'
}

/** C08: offered like the tab's opt-in checkbox, only for a drama in a
 * series and a speaker with a saved name that isn't linked yet. */
export const canRemember = (e: CharacterEntry, hasSeries: boolean) =>
  hasSeries && !e.series_character_id && e.character_name.trim() !== ''
