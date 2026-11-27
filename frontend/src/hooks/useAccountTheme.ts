/*
 * useAccountTheme(): keeps the theme on the signed-in account, so it follows the person to any device.
 * /api/auth/me carries what the account saved. On sign-in that theme wins over this browser's; an
 * account that has none yet takes this browser's (so a choice made before this existed isn't lost).
 * With sign-in off, or for the PC owner, there is no account and the theme stays per browser as before.
 */
import { useEffect } from 'react'

import { putPreferences, type AccountPreferences } from '../api/auth'
import { applyAccountTheme, getThemePref, setAccountThemeSync } from '../theme'

/** Starts keeping the theme on the account; the returned function stops it (sign-out, another account). */
export function startAccountTheme(saved: AccountPreferences | undefined): () => void {
  if (saved?.theme) applyAccountTheme(saved.theme)
  else if (getThemePref() !== 'system') void putPreferences({ theme: getThemePref() }).catch(() => {})
  // A failed save leaves the choice on this browser only; the next sign-in tries again.
  setAccountThemeSync((pref) => void putPreferences({ theme: pref }).catch(() => {}))
  return () => setAccountThemeSync(null)
}

export function useAccountTheme(userId: number | null, saved: AccountPreferences | undefined): void {
  // Only a change of account restarts it; a later /me refresh must not undo a choice made since.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => (userId === null ? undefined : startAccountTheme(saved)), [userId])
}
