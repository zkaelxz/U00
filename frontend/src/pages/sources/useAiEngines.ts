import { useEffect, useState } from 'react'

import { getAiEngines } from '../../api/sourcesExtraction'
import { translateApi } from '../../api/translate'
import { useReadiness } from '../../hooks/useReadiness'
import type { AiEngines } from '../../types/sourcesExtraction'
import type { TranslateEngine } from '../../types/translate'
import { engineGate, ollamaAnswer, type EngineGate } from '../workspace/stages/engineReady'

/**
 * The fallback's engine list, loaded once, when first `enabled` (the AI toggle is on), plus why a listed
 * engine cannot run (no key, package missing, Ollama stopped). The names list carries no key state, so
 * that comes from the shared engine list; if it cannot be read, every engine counts as ready.
 */
export function useAiEngines(enabled: boolean) {
  const [engines, setEngines] = useState<AiEngines | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [state, setState] = useState<TranslateEngine[] | null>(null)
  const [asked, setAsked] = useState(false)
  const { readiness } = useReadiness()
  useEffect(() => {
    if (enabled) setAsked(true)
  }, [enabled])
  // Keyed on `asked` alone: when it was keyed on `engines`, the names list
  // answering first re-ran the effect, whose cleanup dropped the engine list
  // still on its way, so no engine ever showed why it cannot run.
  useEffect(() => {
    if (!asked) return
    let live = true
    getAiEngines().then(
      (r) => live && setEngines(r),
      (e: unknown) => live && setError(e),
    )
    translateApi.engines().then(
      (r) => live && setState(r),
      () => undefined,
    )
    return () => {
      live = false
    }
  }, [asked])
  const gateFor = (name: string | null): EngineGate | null =>
    name ? engineGate(state, name, name === 'ollama' ? ollamaAnswer(readiness) : null) : null
  return { engines, error, gateFor }
}
