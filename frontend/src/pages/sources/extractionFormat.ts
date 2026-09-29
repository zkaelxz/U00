// Pure logic and copy for the pasted-URL extraction extras (parity SO09):
// the AI fallback picker; SO06, the comic import's result. No React here; tested in extractionFormat.test.ts.
import { humanize } from '../../components/labels'
import type { AiEngines, AiRequestFields, ComicUrlImportResult } from '../../types/sourcesExtraction'

export interface AiChoice {
  on: boolean
  // null: the saved default engine.
  engine: string | null
}

export const AI_OFF: AiChoice = { on: false, engine: null }

export const AI_HELP =
  'Only used when Baihe can’t tell which part of the page is the chapter: one AI call for the page, ' +
  'and the site’s layout is remembered so its next chapter needs none. The AI only points at parts of ' +
  'the page; the text and images are copied from the page itself, never rewritten.'

/** The engine the request will use: the one picked, else the saved default. */
export function effectiveEngine(choice: AiChoice, engines: AiEngines | null): string | null {
  if (!choice.on) return null
  if (choice.engine && (!engines || engines.engines.includes(choice.engine))) return choice.engine
  return engines?.default ?? null
}

/** Why the fallback can't be used as set, or null. */
export function aiReason(choice: AiChoice, engines: AiEngines | null): string | null {
  if (!choice.on) return null
  if (!engines) return 'Loading the AI engines…'
  if (engines.engines.length === 0) return 'No AI engine can be used for this.'
  if (!effectiveEngine(choice, engines)) return 'Still needed: an AI engine.'
  return null
}

/** The fields to add to the import request (nothing when the fallback is off). */
export function aiRequestFields(choice: AiChoice, engines: AiEngines | null): AiRequestFields {
  const engine = effectiveEngine(choice, engines)
  return choice.on && engine ? { use_ai: true, engine } : {}
}

export const engineLabel = (name: string) => (name === 'ollama' ? 'Ollama (on this PC)' : humanize('engine', name))

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`

export function comicImportText(r: ComicUrlImportResult): string {
  if (r.needs_review) {
    return 'Baihe couldn’t be sure which images are the pages, so nothing was added.'
  }
  return `Added ${plural(r.pages_added, 'page')} to the drama.`
}

/** The heading of the "left out" list, or null when nothing was left out. */
export function skippedTitle(r: ComicUrlImportResult): string | null {
  if (!r.skipped_count) return null
  const shown = r.skipped.length < r.skipped_count ? ` (first ${r.skipped.length} shown)` : ''
  return `${plural(r.skipped_count, 'image')} left out${shown}`
}
