// Whether the engine a button will call can run now, before the click. Built on the engine list every
// AI picker already reads (key_configured, package_installed) and the readiness answer for Ollama, so
// there is no second source. Anything unknown (list or readiness not loaded) counts as ready: the
// server still refuses what cannot run.
import { engineShortName } from '../../../api/translate'
import type { SetupReadiness } from '../../../types/setup'
import type { TranslateEngine } from '../../../types/translate'
import type { Need } from '../../readiness/needs'

// The SDK each hosted engine imports (services/translate_service._ENGINE_PACKAGES); Diagnostics > Packages names it.
const ENGINE_PACKAGES: Record<string, string> = { claude: 'anthropic', deepseek: 'openai', openai: 'openai' }

export type EngineGateId = 'engine_package' | 'engine_key' | 'engine_ollama'

export interface EngineGate extends Need {
  id: EngineGateId
}

/**
 * Why `engineName` cannot run, as a Need the shared NeedsInstall renders with its fix. Empty name means
 * "the title's engine": pass the resolved name. `ollamaReachable` is null/undefined while unknown.
 */
export function engineGate(
  engines: TranslateEngine[] | null | undefined,
  engineName: string,
  ollamaReachable: boolean | null | undefined,
): EngineGate | null {
  const engine = engines?.find((e) => e.name === engineName)
  if (!engine) return null
  const who = engineShortName(engine)
  if (engine.package_installed === false) {
    const pkg = ENGINE_PACKAGES[engine.name]
    return {
      id: 'engine_package', label: who, action: 'diagnostics', packages: pkg ? [pkg] : [],
      reason: `${who} can't run: the ${pkg ?? 'software'} package it needs isn't installed.`,
    }
  }
  if (engine.name === 'ollama') {
    if (ollamaReachable !== false) return null
    return {
      id: 'engine_ollama', label: 'Ollama', action: 'recheck', packages: [],
      reason: "Ollama isn't answering on this PC. Start it, then check again.",
      link: { href: 'https://ollama.com/download', label: 'Get Ollama' },
    }
  }
  if (engine.key_configured === false) {
    return {
      id: 'engine_key', label: who, action: 'settings', packages: [],
      reason: `${who} has no API key yet, so this can't run.`,
    }
  }
  return null
}

/** The Ollama answer to trust: a stage's own probe (its "Check again" re-reads it) first, else the shared readiness read. */
export function ollamaAnswer(readiness: SetupReadiness | null, configProbe?: boolean | null): boolean | null {
  return configProbe ?? readiness?.ollama_reachable ?? null
}

/** Heading and detail for a refused AI request: the cause when the gate knows it, else the generic key hint. */
export function aiUnavailableText(gate: EngineGate | null): string {
  return gate ? gate.reason : 'AI help is not set up. Add an API key under Settings, then try again.'
}
