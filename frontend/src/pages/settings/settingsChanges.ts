// Which Settings cards are new or moved since this browser last looked, found by comparing the registry in
// settingsIndex.ts with the cards this browser has already seen, so a release needs no list to maintain.
import { useCallback, useMemo, useState } from 'react'

import { browserStorage, readPref, writePref } from '../../hooks/usePersistedState'
import { SETTINGS_TABS } from './settingsIndex'

export type CardChange = 'new' | 'moved'
export type CardChanges = Readonly<Record<string, CardChange>>

const SEEN_KEY = 'settings.seenCards'

/** "tab/card" for every card in the registry; the tab is part of the key so a card that changed tab differs. */
export const registryCardKeys = (): string[] => SETTINGS_TABS.flatMap((t) => t.cards.map((c) => `${t.id}/${c.id}`))

const cardOf = (key: string) => key.slice(key.indexOf('/') + 1)

/** Cards in `current` that `seen` lacks: "moved" when the card was seen under another tab, else "new".
 *  With nothing seen yet (a first visit) nothing is flagged, or every card would be. */
export function changedCards(current: readonly string[], seen: readonly string[] | null): CardChanges {
  if (seen === null) return {}
  const had = new Set(seen)
  const hadCards = new Set(seen.map(cardOf))
  const out: Record<string, CardChange> = {}
  for (const key of current) {
    if (!had.has(key)) out[cardOf(key)] = hadCards.has(cardOf(key)) ? 'moved' : 'new'
  }
  return out
}

/** The flags for this visit, and `seen(id)` to clear one once the person has opened that card.
 *  Storage that is missing or blocked just shows no badges. */
export function useSettingsChanges() {
  const [stored] = useState(() => {
    const storage = browserStorage()
    const seen = readPref<string[] | null>(storage, SEEN_KEY, null)
    // typeof null is 'object', so a missing value reads back as the fallback null.
    if (seen === null) writePref(storage, SEEN_KEY, registryCardKeys())
    return seen
  })
  const [cleared, setCleared] = useState<ReadonlySet<string>>(new Set())
  const changes = useMemo(() => {
    const all = changedCards(registryCardKeys(), stored)
    return Object.fromEntries(Object.entries(all).filter(([id]) => !cleared.has(id))) as CardChanges
  }, [stored, cleared])
  const seen = useCallback((id: string) => {
    setCleared((cur) => {
      if (cur.has(id)) return cur
      const storage = browserStorage()
      const keys = new Set(readPref<string[]>(storage, SEEN_KEY, []))
      for (const key of registryCardKeys()) if (cardOf(key) === id) keys.add(key)
      writePref(storage, SEEN_KEY, [...keys])
      return new Set(cur).add(id)
    })
  }, [])
  return { changes, seen }
}
