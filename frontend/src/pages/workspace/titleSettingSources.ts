// Plain-language rows for "Where settings come from" on a title.
import type { TitleSettingSources } from '../../api/series'

type Row = TitleSettingSources['settings'][number]

function show(value: unknown): string {
  if (value === true) return 'On'
  if (value === false) return 'Off'
  return value === null || value === undefined ? 'not set' : String(value)
}

/** "From the title", "Same as the series default", or "From Settings", plus what the series says when it differs. */
export function sourceText(row: Row): string {
  if (row.source === 'series') return 'Same as the series default'
  if (row.source === 'settings_default') return 'From Settings (the title has none)'
  return row.differs_from_series
    ? `Set on this title (the series default is ${show(row.series_value)})`
    : 'Set on this title'
}

export function sourceRows(data: TitleSettingSources): Array<{ key: string; label: string; value: string; source: string }> {
  return data.settings.map((r) => ({ key: r.key, label: r.label, value: show(r.value), source: sourceText(r) }))
}
