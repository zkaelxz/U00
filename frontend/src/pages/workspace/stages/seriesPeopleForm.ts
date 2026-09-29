import type { SeriesPersonCreate, SeriesPersonUpdate } from '../../../api/seriesPeople'
import type { SeriesCharacter } from '../../../types/autotuneGlossary'

export const PRONOUN_PRESETS = ['she/her', 'he/him', 'they/them'] as const
export const CUSTOM = '__custom__'
const LEGACY: Record<string, string> = { female: 'she/her', male: 'he/him' }

export interface PersonForm {
  character_name: string
  /** '' (unspecified), a preset, or CUSTOM. */
  pronoun_choice: string
  custom_pronouns: string
  aliases: string
  notes: string
}

/** Stored pronoun text as shown (legacy "female"/"male" map to presets,
 * like translation_guide.normalize_pronouns). */
export const normalizePronouns = (p: string) => {
  const v = p.trim()
  return LEGACY[v.toLowerCase()] ?? v
}

export function toPersonForm(c?: SeriesCharacter): PersonForm {
  const p = normalizePronouns(c?.pronouns ?? '')
  const preset = p === '' || (PRONOUN_PRESETS as readonly string[]).includes(p)
  return {
    character_name: c?.character_name ?? '',
    pronoun_choice: preset ? p : CUSTOM,
    custom_pronouns: preset ? '' : p,
    aliases: c?.aliases ?? '',
    notes: c?.notes ?? '',
  }
}

/** The pronoun text the form stands for. Custom with nothing typed keeps
 * `fallback` (the saved value), as the Streamlit picker does. */
export function formPronouns(f: PersonForm, fallback = ''): string {
  if (f.pronoun_choice !== CUSTOM) return f.pronoun_choice
  return f.custom_pronouns.trim() || fallback
}

/** A problem to show before sending, or null. */
export function personProblem(f: PersonForm): string | null {
  if (!f.character_name.trim()) return 'A name cannot be blank.'
  return null
}

/** Only changed fields are sent (omitted = leave alone, "" = clear). */
export function buildPersonUpdate(c: SeriesCharacter, f: PersonForm): SeriesPersonUpdate {
  const u: SeriesPersonUpdate = {}
  const name = f.character_name.trim()
  if (name !== c.character_name) u.character_name = name
  const current = normalizePronouns(c.pronouns)
  const pronouns = formPronouns(f, current)
  if (pronouns !== current) u.pronouns = pronouns
  const aliases = f.aliases.trim()
  if (aliases !== c.aliases) u.aliases = aliases
  const notes = f.notes.trim()
  if (notes !== c.notes) u.notes = notes
  return u
}

export const isPersonDirty = (u: SeriesPersonUpdate) => Object.keys(u).length > 0

export function buildPersonCreate(f: PersonForm): SeriesPersonCreate {
  return {
    character_name: f.character_name.trim(),
    pronouns: formPronouns(f),
    aliases: f.aliases.trim(),
    notes: f.notes.trim(),
  }
}
