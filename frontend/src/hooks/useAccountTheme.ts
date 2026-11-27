/*
 * useAccountTheme(): keeps the theme on the signed-in account, so it follows the person to any device.
 * On sign-in the account's saved theme wins over this browser's; an account that has none yet takes
 * this browser's (so a choice made before this existed isn't lost). With sign-in off, or for the PC
 * owner, there is no account and the theme stays per browser as before.
 */
import { useEffect } from 'react'

import { getPreferences, putPreferences } from '../api/auth'
import { applyAccountTheme, getThemePref, setAccountThemeSync } from '../theme'

/** Starts keeping the theme on the account; the returned function stops it (sign-out, another account). */
export function startAccountTheme(): () => void {
  let live = true
  getPreferences()
    .then((prefs) => {
      if (!live) return
      if (prefs.theme) applyAccountTheme(prefs.theme)
      else if (getThemePref() !== 'system') void putPreferences({ theme: getThemePref() }).catch(() => {})
    })
    .catch(() => {})
  // A failed save leaves the choice on this browser only; the next sign-in tries again.
  setAccountThemeSync((pref) => void putPreferences({ theme: pref }).catch(() => {}))
  return () => {
    live = false
    setAccountThemeSync(null)
  }
}

export function useAccountTheme(userId: number | null): void {
  useEffect(() => (userId === null ? undefined : startAccountTheme()), [userId])
}
