// Reader Listen (api/schemas/reader.py ListenConfig), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type ListenTtsEngine = Schemas['DubTtsEngine']
export type ListenConfig = Schemas['ListenConfig']

export type ListenRunRequest = Schemas['ListenRunRequest']
export type ListenTestVoiceRequest = Schemas['ListenTestVoiceRequest']

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

// Null when narration can be started, otherwise the plain reason it cannot.
export function listenBlocker(cfg: ListenConfig, engine: string): string | null {
  if (cfg.speakable_line_count === 0) return 'There is no text to speak yet.'
  if (cfg.blocker) return cfg.blocker
  if (!engine) return 'No voice engine is available.'
  const picked = cfg.tts_engines.find((t) => t.key === engine)
  return picked?.unavailable_reason ?? missingVoices(picked) ?? null
}

// Only OmniVoice picks voices on its own; the server lists, per engine, the speakers it would have to skip.
function missingVoices(engine: ListenConfig['tts_engines'][number] | undefined): string | null {
  const names = engine?.speakers_without_voice ?? []
  if (names.length === 0) return null
  return `${names.join(', ')} ${names.length === 1 ? 'has' : 'have'} no voice yet. ${engine!.label} needs a reference clip or a voice description for every speaker; only OmniVoice picks voices on its own.`
}

// What a finished run left out: the server lists each skipped line ("line 5: ...") in the job result.
export function skippedLines(job: { result?: { [key: string]: unknown } | null } | null): string[] {
  const errors = job?.result?.errors
  return Array.isArray(errors) ? errors.filter((e): e is string => typeof e === 'string') : []
}
