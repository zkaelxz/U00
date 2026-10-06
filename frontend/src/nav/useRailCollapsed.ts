import { useCallback, useState } from 'react'

import { useMediaQuery } from '../hooks/useMediaQuery'
import { browserStorage, readPref, writePref } from '../hooks/usePersistedState'
import { RAIL_COLLAPSED_KEY, RAIL_EXPANDED_MIN_WIDTH, resolveRailCollapsed } from './navItems'

// readPref returns its fallback only when nothing valid is stored, so two
// different fallbacks tell "never chosen" apart from a saved true or false.
export function readSavedRailChoice(storage: Parameters<typeof readPref>[0]): boolean | null {
  const asTrue = readPref<boolean>(storage, RAIL_COLLAPSED_KEY, true)
  const asFalse = readPref<boolean>(storage, RAIL_COLLAPSED_KEY, false)
  return asTrue === asFalse ? asTrue : null
}

export function useRailCollapsed(): [boolean, () => void] {
  const [saved, setSaved] = useState<boolean | null>(() => readSavedRailChoice(browserStorage()))
  const wideEnough = useMediaQuery(`(min-width: ${RAIL_EXPANDED_MIN_WIDTH}px)`)
  const collapsed = resolveRailCollapsed(saved, wideEnough)
  const toggle = useCallback(() => {
    setSaved(!collapsed)
    writePref(browserStorage(), RAIL_COLLAPSED_KEY, !collapsed)
  }, [collapsed])
  return [collapsed, toggle]
}
