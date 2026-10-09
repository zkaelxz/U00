import type { ClearReadingSpeedFlagsResult, ReadingSpeedMode } from '../../../../types/export'

export const MODE_OPTIONS: { value: ReadingSpeedMode; label: string }[] = [
  { value: 'normal', label: 'Normal' },
  { value: 'relaxed', label: 'Relaxed (fast talkers)' },
  { value: 'off', label: 'Off' },
]

export const MODE_HELP =
  'Normal flags English over about 8.5 characters/second; Relaxed about 12, for fast talkers; Off never flags. Lines flagged earlier keep their flag until you clear them below.'

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

export function clearMessage(r: ClearReadingSpeedFlagsResult, recheck: boolean): string {
  if (r.cleared_count === 0 && r.flagged_count === 0) return 'No reading-speed flags to clear.'
  const cleared = `Cleared ${plural(r.cleared_count, 'reading-speed flag')}`
  const again = recheck ? `, flagged ${plural(r.flagged_count, 'line')} again` : ''
  return `${cleared}${again}. Undo from Records, Line history.`
}
