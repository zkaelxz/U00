import { routeHref } from '../../router'
import type { SetupReadiness } from '../../types/setup'

export const GET_STARTED_PREF = 'library.getStarted.dismissed'

/** The card is for a library with no dramas, until the user dismisses it. The
 *  count is unknown (null) while stats load or when they fail: show nothing. */
export function showGetStarted(totalDramas: number | null | undefined, dismissed: boolean): boolean {
  return totalDramas === 0 && !dismissed
}

export interface FirstRunStep {
  id: 'key' | 'transcription' | 'subtitles'
  label: string
  done: boolean
  /** Where the step goes; null for a step this person can only ask someone else to do. */
  href: string | null
}

export interface FirstRunFacts {
  canSetUp: boolean
  canCreate: boolean
  readiness: Pick<SetupReadiness, 'faster_whisper' | 'ollama_reachable' | 'key_claude' | 'key_deepseek' | 'key_gemini' | 'key_openai' | 'key_groq'> | null
  totalDramas: number
}

export const hasTranslationEngine = (r: NonNullable<FirstRunFacts['readiness']>): boolean =>
  r.ollama_reachable || r.key_claude || r.key_deepseek || r.key_gemini || r.key_openai || r.key_groq

/** The steps this person can act on. The key step goes away once an engine can run (and
 *  while readiness is unknown, so it never flashes up for someone who already has one);
 *  an unknown readiness is not a reason to call transcription installed. */
export function firstRunSteps({ canSetUp, canCreate, readiness, totalDramas }: FirstRunFacts): FirstRunStep[] {
  const steps: FirstRunStep[] = []
  if (canSetUp && readiness && !hasTranslationEngine(readiness)) {
    steps.push({ id: 'key', label: 'Add a translation key', done: false, href: routeHref({ name: 'settings' }) })
  }
  steps.push({
    id: 'transcription',
    label: 'Install transcription',
    done: !!readiness?.faster_whisper,
    href: canSetUp ? routeHref({ name: 'diagnostics', card: 'recommended' }) : null,
  })
  if (canCreate) steps.push({ id: 'subtitles', label: 'Make subtitles', done: totalDramas > 0, href: routeHref({ name: 'make-subtitles' }) })
  return steps
}
