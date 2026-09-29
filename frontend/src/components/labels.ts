// Human labels for raw values the API returns (status, media type, language
// code, engine id), so the UI never shows "streamer_vod" or "zh". Unknown
// values fall back to a tidied form ("new_thing" -> "New thing").

const MEDIA_TYPE: Record<string, string> = {
  audio_drama: 'Audio drama',
  video_drama: 'Video drama',
  anime: 'Anime',
  novel: 'Novel',
  novel_narration: 'Novel narration',
  manhwa: 'Manhwa',
  manga: 'Manga',
  manhua: 'Manhua',
  asmr: 'ASMR',
  streamer_vod: 'Streamer VOD',
  music: 'Music',
  game: 'Game',
  other: 'Other',
}

const LANGUAGE: Record<string, string> = {
  zh: 'Chinese',
  'zh-hans': 'Chinese (Simplified)',
  'zh-hant': 'Chinese (Traditional)',
  ja: 'Japanese',
  ko: 'Korean',
  en: 'English',
}

const ENGINE: Record<string, string> = {
  claude: 'Claude',
  deepseek: 'DeepSeek',
  gemini: 'Gemini',
  deepl: 'DeepL',
  google: 'Google',
  groq: 'Groq',
  openai: 'OpenAI',
  ollama: 'Ollama',
  libretranslate: 'LibreTranslate',
  hf_token: 'Hugging Face token',
  ollama_url: 'Ollama URL',
  libretranslate_url: 'LibreTranslate URL',
  gpt_sovits_url: 'GPT-SoVITS URL',
}

export type LabelKind = 'status' | 'mediaType' | 'language' | 'engine'

const MAPS: Record<LabelKind, Record<string, string>> = {
  status: {},
  mediaType: MEDIA_TYPE,
  language: LANGUAGE,
  engine: ENGINE,
}

// "not started" / "not_started" / "NOT-STARTED" -> "Not started"
export function tidy(raw: string): string {
  const s = raw.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase()
  return s ? s[0].toUpperCase() + s.slice(1) : ''
}

export function humanize(kind: LabelKind, raw: string | null | undefined): string {
  if (raw == null || raw === '') return ''
  return MAPS[kind][raw.toLowerCase()] ?? tidy(raw)
}

export type BadgeTone = 'neutral' | 'accent' | 'ok' | 'warn' | 'bad' | 'info'

// Pipeline status -> badge colour: done states green, in-progress accent/info.
const STATUS_TONE: Record<string, BadgeTone> = {
  'not started': 'neutral',
  aligned: 'info',
  translated: 'accent',
  dubbed: 'accent',
  exported: 'ok',
  running: 'info',
  queued: 'neutral',
  done: 'ok',
  failed: 'bad',
  error: 'bad',
  cancelled: 'warn',
}

export function statusTone(raw: string | null | undefined): BadgeTone {
  return STATUS_TONE[(raw ?? '').toLowerCase().replace(/_/g, ' ')] ?? 'neutral'
}
