// Pure helpers for the PC alerts card (unit-tested): turn the typed text of each row into the change to send.
import type { ResourceAlertChange, ResourceAlertRule } from '../../types/resourceAlerts'

export type AlertDraft = { enabled: boolean; limit: string; minutes: string }

export const draftOf = (r: ResourceAlertRule): AlertDraft => ({
  enabled: r.enabled,
  limit: r.limit === null ? '' : String(r.limit),
  minutes: String(r.minutes),
})

const whole = (text: string): number | null => (/^\d+(\.\d+)?$/.test(text.trim()) ? Number(text) : null)

export function limitProblem(r: ResourceAlertRule, text: string): string | null {
  if (!r.has_limit) return null
  const n = whole(text)
  if (n === null || n < (r.limit_min ?? 0) || n > (r.limit_max ?? Infinity)) {
    return `Use ${r.limit_min} to ${r.limit_max}.`
  }
  return null
}

export function minutesProblem(r: ResourceAlertRule, text: string): string | null {
  const n = whole(text)
  if (n === null || !Number.isInteger(n) || n < r.minutes_min || n > r.minutes_max) {
    return `Use ${r.minutes_min} to ${r.minutes_max}.`
  }
  return null
}

/** The fields that differ from what is saved, or null when nothing changed. Call only when every row is valid. */
export function changeOf(r: ResourceAlertRule, d: AlertDraft): ResourceAlertChange | null {
  const out: ResourceAlertChange = {}
  if (d.enabled !== r.enabled) out.enabled = d.enabled
  if (r.has_limit && Number(d.limit) !== r.limit) out.limit = Number(d.limit)
  if (Number(d.minutes) !== r.minutes) out.minutes = Number(d.minutes)
  return Object.keys(out).length ? out : null
}

export const hasProblem = (r: ResourceAlertRule, d: AlertDraft) =>
  limitProblem(r, d.limit) !== null || minutesProblem(r, d.minutes) !== null

/** "Alerts above 85 °C" style line under a row's name. */
export function ruleSummary(r: ResourceAlertRule): string {
  if (!r.has_limit) return 'Alerts when the card slows itself down to stay safe.'
  const unit = r.unit === '%' ? '%' : ` ${r.unit}`
  return `Alerts ${r.below ? 'below' : 'at or above'} ${r.default_limit}${unit} by default, after ${r.default_minutes} min.`
}
