/*
 * Display labels for stored codes (drama status, media type, source
 * language, translation engine). The API returns raw values such as
 * "streamer_vod" or "deepseek"; show these instead. Unknown codes fall
 * back to a sentence-cased form ("new_thing" -> "New thing"); empty or
 * missing values give ''.
 */

type Code = string | null | undefined

/** "3 lines"; pass `many` for irregular plurals. */
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** Same with a thousands separator, always en-US so the text never depends on the browser locale. */
export const pluralLocale = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`

export const STATUS_LABELS: Record<string, string> = {
  'not started': 'Not started',
  aligned: 'Aligned',
  translated: 'Translated',
  dubbed: 'Dubbed',
  exported: 'Exported',
  running: 'Running',
  queued: 'Queued',
  done: 'Done',
  error: 'Failed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export const MEDIA_TYPE_LABELS: Record<string, string> = {
  audio_drama: 'Audio drama',
  video_drama: 'Video drama',
  anime: 'Anime',
  novel: 'Novel',
  manhwa: 'Manhwa',
  manga: 'Manga',
  manhua: 'Manhua',
  asmr: 'ASMR',
  novel_narration: 'Novel narration',
  streamer_vod: 'Streamer VOD',
  game: 'Game',
  music: 'Music',
  other: 'Other',
}

export const LOCALE_LABELS: Record<string, string> = {
  'en-us': 'English (US)',
  'en-gb': 'English (UK)',
  'en-au': 'English (Australia)',
}

export const LANGUAGE_LABELS: Record<string, string> = {
  zh: 'Chinese',
  'zh-hans': 'Chinese (Simplified)',
  'zh-hant': 'Chinese (Traditional)',
  ja: 'Japanese',
  ko: 'Korean',
  en: 'English',
}

// The stored list tags ('On Hold' ...) are API values; show these instead.
export const TAG_LABELS: Record<string, string> = {
  Favorite: 'Favorite',
  'On Hold': 'On hold',
  'Plan to Translate': 'Plan to translate',
}

export const ENGINE_LABELS: Record<string, string> = {
  claude: 'Claude',
  deepseek: 'DeepSeek',
  gemini: 'Gemini',
  ollama: 'Ollama',
  echo: 'Echo (testing)',
  groq: 'Groq',
  openai: 'OpenAI',
  hf_token: 'Hugging Face token',
  ollama_url: 'Ollama URL',
}

/** "new_thing" / "new-thing" / "new thing" -> "New thing". */
export function sentenceCase(value: Code): string {
  return capFirst((value ?? '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim())
}

// Lower-case product names that stay as written when they start a text.
const KEEP_LOWER = /^(ffmpeg|ffprobe|yt-dlp|pyannote|torch\w*|lightnovel-crawler|lncrawl|ntfy|npm|pip|pytest|faster-whisper|demucs|jiwer)\b/

/** Capitalise the first letter of a display string ("none saved" -> "None saved"); leaves identifiers and tool names alone. */
export function capFirst(text: string): string {
  const first = text.charAt(0)
  if (first < 'a' || first > 'z') return text
  const word = text.split(/\s/, 1)[0]
  if (KEEP_LOWER.test(text) || /[_./\\0-9()@:]/.test(word)) return text
  return first.toUpperCase() + text.slice(1)
}

const lookup = (labels: Record<string, string>) => (value: Code): string => {
  if (!value) return ''
  return labels[value] ?? labels[value.toLowerCase()] ?? sentenceCase(value)
}

/** A size in MB as people say it: whole gigabytes from 1 GB up ("20 GB"), else MB. */
export function formatSizeMb(mb: number): string {
  if (mb >= 1024) return `${Number((mb / 1024).toFixed(1)).toLocaleString('en-US')} GB`
  return `${mb.toLocaleString('en-US')} MB`
}

export const statusLabel = lookup(STATUS_LABELS)
export const mediaTypeLabel = lookup(MEDIA_TYPE_LABELS)
export const languageLabel = lookup(LANGUAGE_LABELS)
export const engineLabel = lookup(ENGINE_LABELS)
export const tagLabel = (value: Code): string => (value ? TAG_LABELS[value] ?? value : '')

/** Whole seconds as a clock: "2:05", or "1:02:05" past an hour. */
export function formatClock(totalSeconds: number): string {
  const s = Math.floor(totalSeconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}
