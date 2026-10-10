/*
 * Pure helpers for the Model health card (ModelHealthCard.tsx):
 * status labels and tones, which rows need attention and in what order, the
 * "Compare in Benchmark Lab" link, the last-check line and plain error text.
 * No React here (modelHealth.test.ts).
 */
import type { EngineCheck, ModelStatus, ModelStatusItem } from '../../api/models'
import { describeError, safeDetail, summarizeEngineFailure } from '../../components/errorMessages'
import { humanize, humanizeValue, type BadgeTone } from '../../components/labels'
import { routeHref } from '../../router'
import { compareParam } from '../benchmark/benchmarkForm'

const STATUS_LABELS: Record<string, string> = {
  retired: 'Retired',
  not_listed: 'No longer listed',
  not_offered: 'Not offered',
  deprecated: 'Deprecated',
  legacy: 'Older model',
  current: 'Current',
  unknown: 'Not checked',
}

const STATUS_TONES: Record<string, BadgeTone> = {
  retired: 'bad',
  not_listed: 'bad',
  not_offered: 'warn',
  deprecated: 'warn',
  legacy: 'info',
  current: 'ok',
  unknown: 'neutral',
}

export const modelStatusLabel = (s: string) => STATUS_LABELS[s] ?? humanizeValue(s)
export const modelStatusTone = (s: string): BadgeTone => STATUS_TONES[s] ?? 'neutral'

// Presets first (the user can act on them here), then workflow tiers, then built-in defaults.
const KIND_ORDER: Record<string, number> = { preset: 0, extension: 1, tier: 2, default: 3 }

/** Most severe first; then presets, tiers, defaults; then by where it is set. */
export function sortModelItems(items: ModelStatusItem[]): ModelStatusItem[] {
  return [...items].sort(
    (a, b) =>
      b.severity - a.severity ||
      (KIND_ORDER[a.kind] ?? 3) - (KIND_ORDER[b.kind] ?? 3) ||
      a.where.localeCompare(b.where) ||
      a.model.localeCompare(b.model),
  )
}

/** Rows shown open (severity 1+: retired, not listed, deprecated, older) and the rest (current, not checked). */
export function splitModelItems(items: ModelStatusItem[]): { attention: ModelStatusItem[]; others: ModelStatusItem[] } {
  const sorted = sortModelItems(items)
  return { attention: sorted.filter((i) => i.severity >= 1), others: sorted.filter((i) => i.severity < 1) }
}

/** The header badge: warnings (severity 2+) as bad/warn, else older models as info, else all fine. */
export function healthBadge(status: Pick<ModelStatus, 'items' | 'warnings'>): { text: string; tone: BadgeTone } {
  const n = status.warnings
  if (n > 0) {
    const bad = status.items.some((i) => i.severity >= 3)
    return { text: `${n} ${n === 1 ? 'warning' : 'warnings'}`, tone: bad ? 'bad' : 'warn' }
  }
  const older = status.items.filter((i) => i.severity === 1).length
  if (older > 0) return { text: `${older} older ${older === 1 ? 'model' : 'models'}`, tone: 'info' }
  return { text: 'No warnings', tone: 'ok' }
}

/** Where the model is set, in plain words ("Claude: built-in default", "Preset: Drama A"). */
export function whereLabel(item: Pick<ModelStatusItem, 'kind' | 'engine' | 'where'>): string {
  if (item.kind === 'default') return `${humanize('engine', item.engine)}: built-in default`
  return item.where
}

/** Defaults and workflow tiers can be replaced from the card (a preset is switched, not replaced). */
export const canChooseModel = (item: Pick<ModelStatusItem, 'kind' | 'key'>): boolean =>
  (item.kind === 'default' || item.kind === 'tier') && !!item.key

/** "You chose X instead of the built-in Y." for a replaced default or tier, else null. */
export function overrideLine(item: Pick<ModelStatusItem, 'model' | 'builtin_model' | 'is_override'>): string | null {
  return item.is_override && item.builtin_model ? `You chose ${item.model} instead of the built-in ${item.builtin_model}.` : null
}

/** Why there is nothing to pick from yet: no provider check, or a check that listed nothing else. */
export function noCandidatesLine(item: Pick<ModelStatusItem, 'candidates'>, checkedAt: string | null | undefined): string | null {
  if ((item.candidates ?? []).length > 0) return null
  return checkedAt
    ? 'The last check listed no other model to choose.'
    : 'Press "Check providers now" first to load the models your provider lists.'
}

/** What the user can do about a row that isn't switched from here. */
export function kindHelp(item: Pick<ModelStatusItem, 'kind' | 'can_switch' | 'replacement'>): string | null {
  if (item.kind === 'extension') return 'Change it in Settings, under the browser extension.'
  if (item.kind === 'preset' && !item.can_switch) {
    return item.replacement
      ? `${item.replacement} isn't offered for this engine yet. To pick another model, change the preset in a title's Translate step.`
      : "To pick another model, change the preset in a title's Translate step."
  }
  return null
}

// A model the provider no longer serves, or the app no longer offers, can't be run.
const NOT_RUNNABLE = new Set(['retired', 'not_listed', 'not_offered'])

/** "#/benchmark?compare=engine:model,engine:replacement", or null when there is
 *  no replacement or the old model can't be run any more (nothing to compare). */
export function compareHref(item: Pick<ModelStatusItem, 'engine' | 'model' | 'replacement' | 'status'>): string | null {
  if (!item.replacement || item.replacement === item.model) return null
  if (NOT_RUNNABLE.has(item.status)) return null
  return routeHref({
    name: 'benchmark',
    compare: compareParam([{ engine: item.engine, model: item.model }, { engine: item.engine, model: item.replacement }]),
  })
}

/** "2026-09-30T14:03:11.5" (UTC, as stored) -> "2026-09-30 14:03 UTC". */
export function formatCheckedAt(iso: string | null | undefined): string | null {
  if (!iso) return null
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(iso)
  return m ? `${m[1]} ${m[2]} UTC` : null
}

export function lastCheckedLine(checkedAt: string | null | undefined): string {
  const when = formatCheckedAt(checkedAt)
  return when ? `Providers last checked ${when}` : 'Providers not checked yet'
}

export const OFFER_MODELS_LABEL = "Also offer models Claude, Gemini and DeepSeek list that this app doesn't know yet"
export const OFFER_MODELS_HELP =
  'Their cost is estimated at the provider\'s highest rate until the app is updated. Uses the list from your last "Check providers now".'

/** The line under the opt-in: what it adds now, or what to do first. */
export function offerModelsNote(status: Pick<ModelStatus, 'checked_at' | 'offer_provider_models' | 'extra_models'>): string | null {
  if (!status.offer_provider_models) return null
  if (!status.checked_at) return 'No check has run yet. Press "Check providers now" to load the lists.'
  const n = Object.values(status.extra_models ?? {}).reduce((sum, ids) => sum + ids.length, 0)
  return n > 0 ? `${n} extra ${n === 1 ? 'model is' : 'models are'} offered from the last check.` : 'The last check listed no extra models.'
}

export interface EngineCheckLine {
  engine: string
  label: string
  ok: boolean
  text: string
  /** The raw failure text (already filtered of keys and paths), for a "Details" fold. */
  detail: string | null
}

/** One line per engine the last check asked: its model count, or why it failed (plain, nothing key- or path-like). */
export function engineCheckLines(engines: Record<string, EngineCheck>): EngineCheckLine[] {
  return Object.entries(engines)
    .map(([engine, c]) => {
      const label = humanize('engine', engine)
      const detail = c.ok ? null : (c.error && safeDetail(c.error)) || null
      return {
        engine,
        label,
        ok: c.ok,
        text: c.ok
          ? `${c.model_count} ${c.model_count === 1 ? 'model' : 'models'} listed`
          : detail
            ? summarizeEngineFailure(engine, c.error, label).summary
            : "Couldn't check: the provider did not answer.",
        detail,
      }
    })
    .sort((a, b) => Number(a.ok) - Number(b.ok) || a.label.localeCompare(b.label))
}

// Codes whose server message is a plain sentence written for people
// ("Models were checked less than a minute ago.", "The preset's model changed since you looked; …").
const PLAIN_CODES = ['rate_limited', 'conflict', 'not_found', 'invalid_input']

/** A failed check or switch as one plain sentence. */
export function modelHealthError(err: unknown): string {
  const e = err as { code?: string; message?: string } | null
  if (e?.code && PLAIN_CODES.includes(e.code) && e.message) {
    const detail = safeDetail(e.message)
    if (detail) return detail
  }
  return describeError(err, { pcOnly: true }).title
}

/** The status read is admin-only, not PC-only: a 403 there means no access. */
export function modelStatusLoadError(err: unknown): string {
  return describeError(err).title
}
