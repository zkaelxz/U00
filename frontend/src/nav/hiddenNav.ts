/*
 * Which menu items a person hid for themselves. Kept in this browser under the
 * signed-in person's id (the PC owner, who has no id, shares one key), so two
 * people on one browser do not see each other's choice. Tidiness only: a hidden
 * page still opens by its address.
 */
import { useCallback, useSyncExternalStore } from 'react'

import { browserStorage, readPref, writePref } from '../hooks/usePersistedState'
import type { SessionState } from '../hooks/useSession'
import { isNavLocked, NAV_ITEMS, navId } from './navItems'

export const hiddenNavKey = (session: SessionState): string => {
  const id = session.status === 'ready' ? session.me.user?.id : null
  return `nav.hidden.${id ?? 'owner'}`
}

/** Only ids the registry still knows and may hide; stale or tampered storage cannot hide Library or Settings. */
export function sanitizeHidden(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  const hideable = new Set(NAV_ITEMS.filter((i) => !isNavLocked(i)).map(navId))
  return [...new Set(raw.filter((v): v is string => typeof v === 'string' && hideable.has(v)))]
}

const listeners = new Set<() => void>()
const cache = new Map<string, { raw: string; value: string[] }>()

function readHidden(key: string): string[] {
  const raw = JSON.stringify(readPref<unknown[]>(browserStorage(), key, []))
  const hit = cache.get(key)
  if (hit?.raw === raw) return hit.value
  const value = sanitizeHidden(JSON.parse(raw))
  cache.set(key, { raw, value })
  return value
}

export function useHiddenNav(session: SessionState): [string[], (id: string, hidden: boolean) => void] {
  const key = hiddenNavKey(session)
  const hidden = useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => readHidden(key),
  )
  const setHidden = useCallback(
    (id: string, hide: boolean) => {
      const next = sanitizeHidden([...readHidden(key).filter((h) => h !== id), ...(hide ? [id] : [])])
      writePref(browserStorage(), key, next)
      listeners.forEach((l) => l())
    },
    [key],
  )
  return [hidden, setHidden]
}
