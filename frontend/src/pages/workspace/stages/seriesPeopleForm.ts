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

// ---- Bulk pronouns (parity X16) ----

/** The bulk picker: '' (nothing chosen yet), CLEAR_PRONOUNS, a preset, or
 * CUSTOM plus typed text. Nothing is chosen at first, so one tap can't clear
 * everyone's pronouns by accident. */
export const CLEAR_PRONOUNS = 'clear'

export interface BulkPronounsForm {
  choice: string
  custom: string
}

export const toBulkPronounsForm = (): BulkPronounsForm => ({ choice: '', custom: '' })

export const peopleCount = (n: number) => `${n} ${n === 1 ? 'person' : 'people'}`

/** Why the bulk button is disabled, or null when it can run. */
export function bulkPronounsProblem(selected: number, f: BulkPronounsForm): string | null {
  if (selected === 0) return 'Still needed: tick at least one person in the list.'
  if (!f.choice) return 'Still needed: choose the pronouns to set.'
  if (f.choice === CUSTOM && !f.custom.trim()) return 'Still needed: type the custom pronouns.'
  return null
}

/** The pronoun text to set ('' clears). */
export const bulkPronouns = (f: BulkPronounsForm) =>
  f.choice === CUSTOM ? f.custom.trim() : f.choice === CLEAR_PRONOUNS ? '' : f.choice

/** Who gets a request: people whose pronouns already match are skipped,
 * so only a change is sent. Order follows `people`, picked by id. */
export function planBulkPronouns(people: SeriesCharacter[], selected: ReadonlySet<number>, f: BulkPronounsForm) {
  const pronouns = bulkPronouns(f)
  const picked = people.filter((p) => selected.has(p.id))
  return {
    pronouns,
    body: { pronouns } as SeriesPersonUpdate,
    send: picked.filter((p) => normalizePronouns(p.pronouns) !== pronouns),
    unchanged: picked.filter((p) => normalizePronouns(p.pronouns) === pronouns),
  }
}

/** One plain-English line for the end of a run. */
export function bulkPronounsSummary(r: { updated: number; unchanged: number; failed: number }, pronouns: string): string {
  const parts: string[] = []
  if (r.updated) parts.push(`Updated ${peopleCount(r.updated)}.`)
  if (r.unchanged) parts.push(`${peopleCount(r.unchanged)} already had ${pronouns ? pronouns : 'no pronouns set'}.`)
  if (r.failed) parts.push(`${peopleCount(r.failed)} could not be updated.`)
  return parts.join(' ') || 'Nothing to update.'
}
