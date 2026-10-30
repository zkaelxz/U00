// Wording for Settings > Update Baihe (SourceUpdateCard). The reasons an update can't run come from the server.
import type { SourceUpdateStatus } from '../../types/update'

export const SOURCE_UPDATE_TITLE = 'Update Baihe'
export const SOURCE_UPDATE_RESTARTING = 'Restarting Baihe. This page reloads its status when Baihe is back.'
export const SOURCE_UPDATE_NOT_BACK = "Baihe hasn't come back yet. Check the PC."

export const sourceVersionLine = (s: SourceUpdateStatus): string | undefined =>
  s.current ? `Version ${s.current}` : undefined

export function sourceCheckLine(s: SourceUpdateStatus): string {
  if (s.behind == null) return s.checked ? "Couldn't check." : 'Not checked yet.'
  if (s.behind === 0) return 'Up to date.'
  return s.behind === 1 ? '1 update waiting.' : `${s.behind} updates waiting.`
}

/** Why an update can't run, shown under the check line unless the copy is already up to date. */
export const sourceReason = (s: SourceUpdateStatus): string | null => (s.behind === 0 ? null : s.reason)
