import { readPref, writePref } from '../../hooks/usePersistedState'
import type { StorageLike } from '../../components/sectionStorage'
import type { LibraryRecap } from '../../types/library'

// The recap of last month shows for the first week of a month only, so it reads as news, not a fixture.
export const RECAP_DAYS = 7

/** Last month (the viewer's own calendar), while this one is in its first week; else null. */
export function recapMonth(now: Date): { month: string; name: string } | null {
  if (now.getDate() > RECAP_DAYS) return null
  const last = new Date(now.getFullYear(), now.getMonth() - 1, 1)
  return {
    month: `${last.getFullYear()}-${String(last.getMonth() + 1).padStart(2, '0')}`,
    name: last.toLocaleString('en', { month: 'long' }),
  }
}

// Per account as well as per month: two people may share a browser.
export function dismissKey(account: number | null, month: string): string {
  return `library.recap.dismissed.${account ?? 'pc'}.${month}`
}

export const isDismissed = (s: StorageLike | null, account: number | null, month: string) =>
  readPref(s, dismissKey(account, month), false)

export const rememberDismissed = (s: StorageLike | null, account: number | null, month: string) =>
  writePref(s, dismissKey(account, month), true)

export type RecapItem = { label: string; value: string }

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many)

/** The numbers worth a line, in reading order; zeroes are left out. */
export function recapItems(r: LibraryRecap): RecapItem[] {
  const items: [number, string, string][] = [
    [r.titles_read, 'title read', 'titles read'],
    [r.titles_finished, 'finished', 'finished'],
    [r.chapters_read, 'chapter read', 'chapters read'],
    [r.moments_saved, 'moment saved', 'moments saved'],
    [r.active_days, 'day with Baihe', 'days with Baihe'],
  ]
  const out = items.filter(([n]) => n > 0).map(([n, one, many]) => ({ value: String(n), label: plural(n, one, many) }))
  if (r.spend_usd) out.push({ value: `$${r.spend_usd.toFixed(2)}`, label: 'spent on this PC' })
  return out
}
