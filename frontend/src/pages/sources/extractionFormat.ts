// Pure logic and copy for the pasted-URL extraction extras (parity SO09):
// the AI fallback picker. No React here; tested in extractionFormat.test.ts.
import { humanize } from '../../components/labels'
import type { AiEngines, AiRequestFields } from '../../types/sourcesExtraction'

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
