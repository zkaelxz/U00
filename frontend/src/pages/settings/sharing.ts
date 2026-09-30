// Pure helpers for Settings > Sharing (SharingCard.tsx).
import { ApiError } from '../../api/client'
import type { SessionState } from '../../hooks/useSession'
import type { SetPrivateResult, SharingItem } from '../../types/sharing'

export const SHARE_DEFAULT_LABEL = 'New items I create are shared with the household'

/** The plain line under the share-by-default switch. */
export function shareDefaultHelp(on: boolean, admin: boolean): string {
  const now = on
    ? 'On: new dramas and series you create can be seen by everyone in the household.'
    : 'Off: new dramas and series you create are private to you (admins can still see them).'
  const existing = admin
    ? 'This only affects new items. Change existing ones one at a time in the list below.'
    : 'This only affects new items. It does not change anything you already have; an admin can change those one at a time.'
  return `${now} ${existing}`
}

/** Whether to show every item (admins, and the owner at the PC with sign-in off).
 * Unknown session (older API): try, and hide the list if the server says no. */
export function canSeeAllItems(s: SessionState): boolean | null {
  if (s.status === 'loading') return null
  if (s.status === 'unavailable') return true
  return s.me.permissions.includes('admin.library')
}

/** A drama in a series has no flag of its own: it follows the series. */
export function followsSeries(item: SharingItem): boolean {
  return item.kind === 'drama' && item.series_id !== null
}

/** Whether the household can see this item right now. */
export function isShared(item: SharingItem): boolean {
  return followsSeries(item) ? !item.series_is_private : !item.is_private
}

export function statusLabel(item: SharingItem): 'Shared' | 'Private' {
  return isShared(item) ? 'Shared' : 'Private'
}

export function itemTitle(item: SharingItem): string {
  return item.title.trim() || (item.kind === 'series' ? 'Untitled series' : 'Untitled drama')
}

export function seriesNote(item: SharingItem): string {
  const name = item.series_name?.trim() || 'its series'
  return `In the series “${name}”. Dramas in a series follow the series; change the series instead.`
}

/** Apply a flip the server confirmed; a series also updates its dramas. */
export function applyFlip(items: SharingItem[], r: SetPrivateResult): SharingItem[] {
  return items.map((i) => {
    if (i.kind === r.kind && i.id === r.id) return { ...i, is_private: r.is_private }
    if (r.kind === 'series' && i.kind === 'drama' && i.series_id === r.id) return { ...i, series_is_private: r.is_private }
    return i
  })
}

export const PC_ITEMS_NOTE =
  'Items created at the PC, or while sign-in was off, have no owner and are saved as private. ' +
  'When sign-in is turned on, others in the household will not see them until an admin shares them here.'

/** "Created at the PC" items the household can't see yet: the ones to review before sign-in. */
export function isPcPrivate(item: SharingItem): boolean {
  return item.created_at_pc && !isShared(item)
}

/** Only private PC items, keeping a series' heading when one of its dramas is shown. */
export function filterPcPrivate(items: SharingItem[]): SharingItem[] {
  const series = new Set(items.filter((i) => i.kind === 'drama' && isPcPrivate(i)).map((i) => i.series_id))
  return items.filter((i) => isPcPrivate(i) || (i.kind === 'series' && series.has(i.id)))
}

export const itemKey = (i: { kind: string; id: number }) => `${i.kind}:${i.id}`

/** Append the next page, skipping anything already shown (items may shift between pages). */
export function mergePage(items: SharingItem[], page: SharingItem[]): SharingItem[] {
  const seen = new Set(items.map(itemKey))
  return [...items, ...page.filter((i) => !seen.has(itemKey(i)))]
}

/** The server's own words (e.g. the 409 "Make the whole series private instead"). */
export function sharingErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403 && e.message === 'Not allowed.') return "You don't have permission to change this."
    return e.message || 'Something went wrong. Please try again.'
  }
  return "Couldn't reach Baihe. Check the connection and try again."
}

export const isForbidden = (e: unknown) => e instanceof ApiError && e.status === 403
