import { useEffect, useState } from 'react'

import { withSignal } from '../../../api/client'
import { getTranslateConfig } from '../../../api/translateStage'
import { useReadiness } from '../../../hooks/useReadiness'
import type { TranslateEngine } from '../../../types/translate'
import type { TranslateRunConfig } from '../../../types/translateStage'
import { engineGate, ollamaAnswer, type EngineGate } from './engineReady'

/** The gate for the engine a button will call (`engineName` already resolved to the title's default when blank). */
export function useEngineGate(
  engines: TranslateEngine[] | null | undefined,
  engineName: string,
  configOllama?: boolean | null,
): EngineGate | null {
  const { readiness } = useReadiness()
  return engineGate(engines, engineName, engineName === 'ollama' ? ollamaAnswer(readiness, configOllama) : null)
}

/** The title's engine list and defaults, for controls that have no picker of their own (one read per mount). */
export function useTitleEngines(dramaId: number) {
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  useEffect(() => {
    const ctl = new AbortController()
    getTranslateConfig(dramaId, withSignal(ctl.signal)).then(
      (c) => !ctl.signal.aborted && setConfig(c),
      () => undefined,
    )
    return () => ctl.abort()
  }, [dramaId])
  return {
    engines: config?.engines ?? null,
    translationEngine: config?.translation_engine ?? '',
    lineAiEngine: config?.line_ai_engine ?? config?.translation_engine ?? '',
  }
}
