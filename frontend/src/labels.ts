/*
 * Display labels for stored codes (drama status, media type, source
 * language, translation engine). The API returns raw values such as
 * "streamer_vod" or "deepseek"; show these instead. Unknown codes fall
 * back to a title-cased form ("new_thing" -> "New Thing"); empty or
 * missing values give ''.
 */

type Code = string | null | undefined

const STATUS_LABELS: Record<string, string> = {
  'not started': 'Not started',
  aligned: 'Aligned',
  translated: 'Translated',
  dubbed: 'Dubbed',
  exported: 'Exported',
}

const MEDIA_TYPE_LABELS: Record<string, string> = {
  audio_drama: 'Audio drama',
  video_drama: 'Video drama',
  anime: 'Anime',
  novel: 'Novel',
  manhwa: 'Manhwa',
  manga: 'Manga',
  manhua: 'Manhua',
  asmr: 'ASMR',
  streamer_vod: 'Streamer VOD',
  music: 'Music',
  other: 'Other',
}

const LANGUAGE_LABELS: Record<string, string> = {
  zh: 'Chinese',
  ja: 'Japanese',
  ko: 'Korean',
  en: 'English',
}

const ENGINE_LABELS: Record<string, string> = {
  claude: 'Claude',
  deepseek: 'DeepSeek',
  gemini: 'Gemini',
  ollama: 'Ollama',
  deepl: 'DeepL',
  google: 'Google',
  nllb: 'NLLB',
  libretranslate: 'LibreTranslate',
  test_offline: 'Offline test',
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
