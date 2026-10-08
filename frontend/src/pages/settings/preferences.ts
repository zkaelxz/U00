/*
 * Pure helpers for Settings > preferences (inventory G05, G06 URLs, G08,
 * G09, G13, G14, G15). The server re-validates everything; these only give
 * an early, plain message and build a patch of the changed fields.
 */
import type { EndpointName, PreferenceKey, SettingsPreferences } from '../../types/settings'

// The one note under every key, address or token: where it goes, and that it stays hidden.
export const SAVED_ON_PC_NOTE = 'Saved on the Baihe PC and never shown again.'

export const ENDPOINTS: { name: EndpointName; label: string; placeholder: string; help: string }[] = [
  {
    name: 'ollama_url',
    label: 'Ollama URL',
    placeholder: 'http://127.0.0.1:11434',
    help: 'Where Ollama runs. Blank uses its default on this PC.',
  },
]

export const OCR_LABELS: Record<string, string> = {
  auto: 'Auto (by source language)',
  manga_ocr: 'manga_ocr (Japanese)',
  paddle: 'PaddleOCR (Chinese, Korean)',
  paddle_vl_manga: 'PaddleOCR-VL for manga (Japanese)',
  tesseract: 'Tesseract',
}

/** The fields of `draft` whose value differs from `saved`. */
export function changedPreferences(
  saved: SettingsPreferences,
  draft: Partial<SettingsPreferences>,
): Partial<SettingsPreferences> {
  const out: Partial<SettingsPreferences> = {}
  for (const key of Object.keys(draft) as PreferenceKey[]) {
    if (draft[key] !== saved[key]) (out as Record<string, unknown>)[key] = draft[key]
  }
  return out
}

export type Parsed<T> = { ok: true; value: T } | { ok: false; error: string }

/** Monthly cap in USD. Blank means "use .env" (null); 0 means no cap. */
export function parseCap(raw: string): Parsed<number | null> {
  const t = raw.trim()
  if (!t) return { ok: true, value: null }
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return { ok: false, error: 'Enter an amount like 20 or 12.50.' }
  const n = Number(t)
  if (n > 1_000_000) return { ok: false, error: 'That cap is too large.' }
  return { ok: true, value: n }
}

const MAX_NUM_CTX = 1_048_576

/** Ollama num_ctx override: blank or 0 means automatic. */
export function parseNumCtx(raw: string): Parsed<number> {
  const t = raw.trim()
  if (!t) return { ok: true, value: 0 }
  if (!/^\d+$/.test(t)) return { ok: false, error: 'Enter a whole number of tokens, or 0.' }
  const n = Number(t)
  if (n > MAX_NUM_CTX) return { ok: false, error: `At most ${MAX_NUM_CTX.toLocaleString('en-US')}.` }
  return { ok: true, value: n }
}

export const MIN_UPLOAD_MB = 100
export const MAX_UPLOAD_MB = 1_048_576

/** Upload size limit in MB (mirrors settings_service._check_upload_mb). Blank puts the default back. */
export function parseUploadMb(raw: string, defaultMb: number): Parsed<number> {
  const t = raw.trim()
  if (!t) return { ok: true, value: defaultMb }
  const n = Number(t)
  if (!/^\d+$/.test(t) || n < MIN_UPLOAD_MB || n > MAX_UPLOAD_MB) {
    return { ok: false, error: `Enter a whole number of MB from ${MIN_UPLOAD_MB} to ${MAX_UPLOAD_MB.toLocaleString('en-US')}.` }
  }
  return { ok: true, value: n }
}

export const DEFAULT_UPLOAD_MB = 20480

const MAX_PATH = 1024

/** A file or folder path on the Baihe PC: one line, not too long. */
export function checkPath(raw: string): string | null {
  if (raw.length > MAX_PATH) return 'That path is too long.'
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(raw)) return 'The path contains characters that are not allowed.'
  return null
}

/** Mirrors settings_service.validate_endpoint_url: returns an error or null. */
export function checkEndpointUrl(raw: string): string | null {
  const t = raw.trim()
  if (!t) return 'Enter a URL, or use Clear.'
  if (t.length > 300) return 'The URL is too long.'
  // eslint-disable-next-line no-control-regex
  if (/[\s\u0000-\u001f\u007f"'\\]/.test(t)) return 'The URL contains characters that are not allowed.'
  let u: URL
  try {
    u = new URL(t)
  } catch {
    return 'The URL is not valid.'
  }
  if ((u.protocol !== 'http:' && u.protocol !== 'https:') || !u.hostname)
    return 'The URL must start with http:// or https:// and name a host.'
  if (u.username || u.password || t.includes('@')) return 'The URL must not contain a user name or password.'
  if (t.includes('?') || t.includes('#')) return 'The URL must not contain a query or fragment.'
  return null
}

export function capSummary(saved: number | null, envCap: number): string {
  if (saved === null) return envCap > 0 ? `$${envCap.toFixed(2)} (from .env)` : 'no cap'
  return saved > 0 ? `$${saved.toFixed(2)} a month` : 'no cap'
}

export function cookiesSummary(browser: string | null, file: string): string {
  if (file) return 'cookies.txt file'
  if (browser) return `from ${browser}`
  return 'none'
}
