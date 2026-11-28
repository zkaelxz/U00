// Converts between a declared setting's saved value and the text or toggle its field edits.
import type { SettingsSchemaRow } from '../../types/settings'
import { checkPath, type Parsed } from './preferences'

// An unset number reads as blank: the placeholder says what blank means. A zero reads as blank only where blank
// saves back as 0; elsewhere ("0 = never" over a default of 2, "0 = no cap" over the .env cap) it must stay 0, or
// the next save of the card would quietly put the default back.
export function toDraftValue(row: SettingsSchemaRow, saved: unknown): string | boolean {
  if (row.type === 'bool') return saved === true
  if (row.type === 'int' || row.type === 'float') return saved == null || (saved === 0 && row.default === 0) ? '' : String(saved)
  if (row.type === 'json') return JSON.stringify(saved ?? row.default ?? {}, null, 2)
  return saved == null ? '' : String(saved)
}

export function blockedPhrasesSummary(text: string | null | undefined): string {
  const n = (text ?? '').split('\n').filter((l) => l.trim()).length
  return n === 0 ? 'Nothing blocked' : `${n} phrase${n === 1 ? '' : 's'}`
}

export function toSavedValue(row: SettingsSchemaRow, raw: string | boolean): Parsed<unknown> {
  if (row.type === 'bool') return { ok: true, value: raw === true }
  const text = String(raw).trim()
  if (row.type === 'choice') return { ok: true, value: text || row.default }
  if (row.type === 'int' || row.type === 'float') {
    if (!text) return { ok: true, value: row.default }
    const whole = row.type === 'int'
    const range =
      row.min != null && row.max != null ? ` from ${row.min.toLocaleString('en-US')} to ${row.max.toLocaleString('en-US')}` : ''
    const n = Number(text)
    if (!(whole ? /^-?\d+$/ : /^-?\d+(\.\d+)?$/).test(text) || (row.min != null && n < row.min) || (row.max != null && n > row.max)) {
      return { ok: false, error: whole ? `Enter a whole number${range}.` : `Enter an amount${range}.` }
    }
    return { ok: true, value: n }
  }
  if (row.type === 'json') {
    try {
      return { ok: true, value: JSON.parse(text) }
    } catch {
      return { ok: false, error: `${row.label} is not valid JSON.` }
    }
  }
  if (row.max_len != null && text.length > row.max_len) return { ok: false, error: `${row.label} is too long.` }
  if (row.type === 'path') {
    const problem = checkPath(text)
    if (problem) return { ok: false, error: problem }
  }
  return { ok: true, value: text }
}
