import type { VdbNamesStatus } from '../../../types/vdbNames'

export function vdbNamesSummary(status: VdbNamesStatus | null, enabled: boolean): string {
  if (!status) return ''
  if (!status.downloaded) return 'not downloaded'
  return `${status.name_count} names · ${enabled ? 'on for this title' : 'off for this title'}`
}

export function vdbAgeText(status: VdbNamesStatus): string {
  const days = status.age_days
  const age = days == null ? 'Downloaded' : days === 0 ? 'Downloaded today' : `Downloaded ${days} day${days === 1 ? '' : 's'} ago`
  return status.newer_than_30_days ? age : `${age}. Download again to refresh it.`
}

/** Why the title switch is off-limits, or null when it can be used. */
export function vdbToggleBlocker(status: VdbNamesStatus | null): string | null {
  return status && !status.downloaded ? 'Download the VTuber names first.' : null
}
