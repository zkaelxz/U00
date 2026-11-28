/** Groups the downloaded Hugging Face repos by what they are for, with a size total and a sort, for the Models list. */
import type { DiagnosticsHfCacheEntry } from '../../types/diagnostics'

export type ModelKind = 'whisper' | 'speech' | 'translation' | 'other'
export type ModelSort = 'size' | 'name'

export const MODEL_KINDS: { id: ModelKind; label: string }[] = [
  { id: 'whisper', label: 'Whisper' },
  { id: 'speech', label: 'Speech and separation' },
  { id: 'translation', label: 'Translation and text' },
  { id: 'other', label: 'Other' },
]

// Only the name says what a repo is for, so a repo that matches nothing stays in Other rather than being guessed at.
const SPEECH = /asr|speech|voice|tts|wav2vec|pyannote|separat|roformer|demucs|audio|sensevoice|funasr|funaudio|chatterbox|omnivoice|silero|vad|diariz|breeze|kotoba|nemotron|qwen3-?tts/i
const TRANSLATION = /gemma|llama|qwen|opus-mt|nllb|m2m|mbart|\bt5\b|translat|marian|mistral|phi-|bge|e5-|embed|deepseek/i

export function modelKind(repoId: string): ModelKind {
  if (/whisper/i.test(repoId)) return 'whisper'
  if (SPEECH.test(repoId)) return 'speech'
  if (TRANSLATION.test(repoId)) return 'translation'
  return 'other'
}

export function sortModels(items: DiagnosticsHfCacheEntry[], by: ModelSort): DiagnosticsHfCacheEntry[] {
  const key = (e: DiagnosticsHfCacheEntry) => `${e.repo_id}@${e.revision}`
  return [...items].sort((a, b) => (by === 'size' ? b.size_bytes - a.size_bytes : 0) || key(a).localeCompare(key(b)))
}

export interface ModelGroupData {
  id: ModelKind
  label: string
  items: DiagnosticsHfCacheEntry[]
  totalBytes: number
}

/** Non-empty groups in a fixed order, each sorted; the empty ones are left out. */
export function groupModels(items: DiagnosticsHfCacheEntry[], by: ModelSort): ModelGroupData[] {
  return MODEL_KINDS.map(({ id, label }) => {
    const inGroup = sortModels(items.filter((e) => modelKind(e.repo_id) === id), by)
    return { id, label, items: inGroup, totalBytes: inGroup.reduce((n, e) => n + e.size_bytes, 0) }
  }).filter((g) => g.items.length > 0)
}
