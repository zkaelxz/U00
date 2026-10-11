/*
 * Pure model for Settings > Timing and splitting: which declared rows form each group,
 * how a value and its default read, and what a Reset sends. The rows come from
 * GET /api/settings/schema; the server re-validates every write.
 */
import type { SettingsOverview, SettingsSchemaRow } from '../../types/settings'

export const TIMING_SECTIONS = ['Voice detection', 'Line splitting', 'Sentence grouping'] as const
export type TimingSection = (typeof TIMING_SECTIONS)[number]

export const TIMING_NOTE = 'A changed value only affects runs you start afterwards. Lines you already have are not changed.'
export const DEV_ONLY_NOTE = 'More settings appear here when Developer Mode is on.'

export interface TimingGroup {
  section: TimingSection
  rows: SettingsSchemaRow[]
}

/** The groups in page order. A Developer Mode row shows only while Developer Mode is on, and a group with no rows is left out. */
export function timingGroups(schema: SettingsSchemaRow[] | null, developerMode: boolean): TimingGroup[] {
  return TIMING_SECTIONS.map((section) => ({
    section,
    rows: (schema ?? []).filter((r) => r.section === section && !r.custom && (developerMode || !r.dev_only)),
  })).filter((g) => g.rows.length > 0)
}

export function savedNumber(settings: SettingsOverview, row: SettingsSchemaRow): number {
  const prefs = settings.preferences as unknown as Record<string, unknown>
  const v = prefs[row.key]
  return typeof v === 'number' ? v : (row.default as number)
}

/** "8 s", "40 characters", "0.35". */
export function formatValue(row: SettingsSchemaRow, value: number): string {
  return row.unit ? `${value} ${row.unit}` : String(value)
}

export function defaultLine(row: SettingsSchemaRow): string {
  return `Default: ${formatValue(row, row.default as number)}`
}

export function isDefault(row: SettingsSchemaRow, value: number): boolean {
  return value === row.default
}

/** The draft text a saved number shows. Unlike SchemaCard, 0 is shown as "0": for these rows it is a real value. */
export function draftText(value: number): string {
  return String(value)
}

/** What Reset sends for a row: the declared default. */
export function resetPatch(row: SettingsSchemaRow): Record<string, number> {
  return { [row.key]: row.default as number }
}
