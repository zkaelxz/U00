import type {
  SeriesCopyTermsPreview, SeriesDefaultsField, SeriesDefaultsPreview, SeriesDeletePreview, SeriesListItem, SettingValue,
} from '../../api/series'
import { plural } from '../../labels'

// The Series page tabs. A deep link names one ("#/series/3?tab=glossary").
export type SeriesTab = 'titles' | 'defaults' | 'glossary' | 'settings'

export const SERIES_TABS: { id: SeriesTab; label: string }[] = [
  { id: 'titles', label: 'Titles' },
  { id: 'defaults', label: 'Defaults' },
  { id: 'glossary', label: 'Glossary tools' },
  { id: 'settings', label: 'Settings' },
]

export const isSeriesTab = (v: unknown): v is SeriesTab => SERIES_TABS.some((t) => t.id === v)

// Same limit as a new series' name (services/series_service.py MAX_NAME_LEN).
export const MAX_SERIES_NAME = 300

// How the API compares names and glossary terms: trim, NFKC, case fold.
export const nameKey = (s: string) => s.normalize('NFKC').trim().toLowerCase()

/** Why a rename can't be sent yet, or null. `others`: the other series the viewer sees. */
export function renameProblem(name: string, current: string, others: Pick<SeriesListItem, 'id' | 'name'>[]): string | null {
  const clean = name.trim()
  if (!clean) return 'Enter a name.'
  if (clean.length > MAX_SERIES_NAME) return `A series name is at most ${MAX_SERIES_NAME} characters.`
  if (clean === current) return 'That is already its name.'
  const clash = others.find((s) => nameKey(s.name) === nameKey(clean))
  return clash ? `There is already a series called “${clash.name}”.` : null
}

/** The lines of the delete prompt: what goes and what stays. */
export function deleteLines(p: SeriesDeletePreview): { deleted: string[]; kept: string | null } {
  const deleted = [
    p.glossary_term_count > 0 && plural(p.glossary_term_count, 'glossary term'),
    p.character_count > 0 && plural(p.character_count, 'series character'),
    p.has_instructions && 'the series instructions',
    p.translation_memory_count > 0 && plural(p.translation_memory_count, 'translation memory entry', 'translation memory entries'),
    p.dismissal_count > 0 && plural(p.dismissal_count, 'dismissed glossary suggestion'),
    p.style_profile_count > 0 && 'the learned style profile',
  ].filter((x): x is string => !!x)
  const kept = p.title_count > 0 ? `${plural(p.title_count, 'title')} kept: ${p.title_count === 1 ? 'it becomes' : 'they become'} “No series”.` : null
  return { deleted, kept }
}

/** Why Delete can't run, or null. */
export function deleteBlocked(p: SeriesDeletePreview | null, pc: 'local' | 'remote' | 'unknown'): string | null {
  if (pc === 'remote') return 'Deleting is PC only.'
  if (!p) return null
  if (p.job_running) return 'A job is running for a title in this series. Wait for it to finish or cancel it first.'
  return null
}

/** Series the viewer can copy terms from: every other series with at least one term, by name. */
export function copySources(all: SeriesListItem[], currentId: number): SeriesListItem[] {
  return all
    .filter((s) => s.id !== currentId && s.glossary_term_count > 0)
    .sort((a, b) => a.name.localeCompare(b.name))
}

export function copyPreviewText(p: SeriesCopyTermsPreview): string {
  if (p.new_count === 0) return `Nothing to add: all ${plural(p.skipped_count, 'term')} are already there.`
  const added = `${plural(p.new_count, 'new term')} will be added`
  return p.skipped_count > 0
    ? `${added}, ${p.skipped_count} ${p.skipped_count === 1 ? 'is' : 'are'} already there and ${p.skipped_count === 1 ? 'is' : 'are'} skipped.`
    : `${added}.`
}

export const copyResultText = (r: { added: number; skipped: number }) =>
  `Added ${plural(r.added, 'term')}${r.skipped ? `; skipped ${r.skipped} already there` : ''}.`

// ---- Defaults tab -----------------------------------------------------------

/** The form holds strings: '' = not set, 'true'/'false' for on/off, the number as typed. */
export type DefaultsForm = Record<string, string>

export function defaultsForm(fields: SeriesDefaultsField[], settings: Record<string, SettingValue>): DefaultsForm {
  return Object.fromEntries(fields.map((f) => [f.key, f.key in settings ? String(settings[f.key]) : '']))
}

/** The settings to save, or the first problem (checked again by the server). */
export function settingsFromForm(fields: SeriesDefaultsField[], form: DefaultsForm):
  { settings: Record<string, SettingValue> } | { problem: string } {
  const settings: Record<string, SettingValue> = {}
  for (const f of fields) {
    const raw = (form[f.key] ?? '').trim()
    if (raw === '') continue
    if (f.type === 'bool') settings[f.key] = raw === 'true'
    else if (f.type === 'choice') settings[f.key] = raw
    else {
      const n = Number(raw)
      if (!Number.isFinite(n) || (f.type === 'int' && !Number.isInteger(n))) {
        return { problem: `${f.label}: enter ${f.type === 'int' ? 'a whole number' : 'a number'}.` }
      }
      if ((f.min != null && n < f.min) || (f.max != null && n > f.max)) {
        return { problem: `${f.label}: between ${f.min} and ${f.max}.` }
      }
      settings[f.key] = n
    }
  }
  return { settings }
}

export const shownValue = (v: SettingValue | null) => (v === null ? 'app default' : v === true ? 'on' : v === false ? 'off' : String(v))

export function defaultsPreviewText(p: SeriesDefaultsPreview): string {
  if (p.title_count === 0) return 'This series has no titles yet.'
  if (p.changed_titles === 0) return 'Nothing to change: every title already matches.'
  const titles = p.changed_titles === 1 ? '1 title' : `${p.changed_titles} titles`
  const fields = p.fields_changed === 1 ? '1 setting' : `${p.fields_changed} settings`
  return `${titles} of ${p.title_count} would change (${fields} in all).`
}
