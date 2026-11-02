// Reader Listen (api/schemas/reader.py ListenConfig), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type ListenTtsEngine = Schemas['DubTtsEngine']
export type ListenConfig = Schemas['ListenConfig']

export interface ListenRunRequest {
  tts_engine: string
  narration_language?: string
}

// The engine Listen starts with: the one picked here or the server's default while it can run,
// else the first engine that can, so the button never opens on "not installed" when another works.
export function chooseEngine(engines: ListenConfig['tts_engines'], defaultKey: string, picked: string): string {
  const runnable = (key: string) => engines.some((t) => t.key === key && !t.unavailable_reason)
  if (runnable(picked)) return picked
  if (runnable(defaultKey)) return defaultKey
  const first = engines.find((t) => !t.unavailable_reason)
  if (first) return first.key
  return engines.some((t) => t.key === defaultKey) ? defaultKey : (engines[0]?.key ?? '')
}
