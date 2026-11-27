/* How a Quick read run is named in the Jobs list. */
import type { QuickReadRun } from '../../api/jobs'

export function runLabel(run: QuickReadRun): string {
  const where = [run.site, run.chapter].filter(Boolean).join(' / ')
  return where || 'A Quick read'
}
