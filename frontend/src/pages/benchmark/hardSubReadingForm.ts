/*
 * Pure helpers for the hard-sub reading result (HardSubReadingSection.tsx): plain-words lines for a
 * finished reading. No React, so they are tested without a browser (hardSubReadingForm.test.ts).
 */
import type { HardSubReadingOutcome } from '../../types/benchmark'

const percent = (v: number | null) => (v == null ? 'n/a' : `${Math.round(v * 1000) / 10}%`)

export const STOP_REASONS: Record<string, string> = {
  cap: 'Stopped at the spending limit.',
  cancelled: 'Stopped before the end.',
  failed: 'Stopped after repeated failed reads.',
}

/** "Read 40 of 40 stretches. Cost $0.22." plus why it stopped early, if it did. */
export function describeReading(r: HardSubReadingOutcome): string {
  const base = `Read ${r.windows_read} of ${r.windows_total} stretches. Cost $${r.cost_usd.toFixed(2)}.`
  if (!r.stopped) return base
  return `${base} ${STOP_REASONS[r.stopped] ?? 'Stopped early.'}${r.note ? ` ${r.note}` : ''}`
}

/** One line per speaker group: how many lines were read and how much of its text the transcript has. */
export function groupRows(r: HardSubReadingOutcome): { key: string; lines: number; characters: number; recall: string }[] {
  return r.groups.map((g) => {
    const score = r.comparison.groups.find((s) => s.key === g.key)
    return { key: g.key, lines: g.lines.length, characters: score?.characters ?? 0, recall: percent(score?.recall ?? null) }
  })
}

export function describeOverall(r: HardSubReadingOutcome): string {
  const c = r.comparison
  return `The transcript has ${percent(c.recall)} of the on-screen characters; ${percent(c.precision)} of its own characters are on screen. Its speaker labels match the groups for ${percent(c.label_match)} of the shared time.`
}

/** "m:ss" for a time in seconds. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** The names to send with "put speakers on lines": only the colour groups with something typed or a
 * remembered name being cleared are sent, trimmed; an untouched box sends nothing. */
export function namesToSend(typed: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(typed)) if (value !== undefined) out[key] = value.trim()
  return out
}

/** "3 lines labelled, 1 kept (set by hand), 2 not placed." */
export function describeApply(r: { labelled: number; kept_manual: number; unplaced: number }): string {
  return `${r.labelled} lines labelled, ${r.kept_manual} kept (set by hand), ${r.unplaced} not placed.`
}

/** "2 lines changed, 1 skipped (edited since)." */
export function describeAccept(r: { changed: number; skipped: number }): string {
  return `${r.changed} lines changed${r.skipped ? `, ${r.skipped} skipped (edited since)` : ''}.`
}
