/*
 * Display labels for stored codes (drama status, media type, source
 * language, translation engine). The API returns raw values such as
 * "streamer_vod" or "deepseek"; show these instead. Unknown codes fall
 * back to a title-cased form ("new_thing" -> "New Thing"); empty or
 * missing values give ''.
 */

type Code = string | null | undefined

export const STATUS_LABELS: Record<string, string> = {
  'not started': 'Not started',
  aligned: 'Aligned',
  translated: 'Translated',
  dubbed: 'Dubbed',
  exported: 'Exported',
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

export const ENGINE_LABELS: Record<string, string> = {
  claude: 'Claude',
  deepseek: 'DeepSeek',
  gemini: 'Gemini',
  ollama: 'Ollama',
  deepl: 'DeepL',
  google: 'Google',
  nllb: 'NLLB',
  libretranslate: 'LibreTranslate',
  groq: 'Groq',
  openai: 'OpenAI',
  hf_token: 'Hugging Face token',
  ollama_url: 'Ollama URL',
  libretranslate_url: 'LibreTranslate URL',
  gpt_sovits_url: 'GPT-SoVITS URL',
}

/** "new_thing" / "new-thing" / "new thing" -> "New Thing". */
export function titleCase(value: Code): string {
  return (value ?? '')
    .replace(/[_-]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

const lookup = (labels: Record<string, string>) => (value: Code): string => {
  if (!value) return ''
  return labels[value] ?? labels[value.toLowerCase()] ?? titleCase(value)
}

export const statusLabel = lookup(STATUS_LABELS)
export const mediaTypeLabel = lookup(MEDIA_TYPE_LABELS)
export const languageLabel = lookup(LANGUAGE_LABELS)
export const engineLabel = lookup(ENGINE_LABELS)
