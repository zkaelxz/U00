/* Whether Live shows only the player and the lines: kept on the signed-in account so it follows the person to any device, and on this browser as the fallback (sign-in off, the PC owner, a failed save). */
import { useEffect } from 'react'

import { putPreferences } from '../../api/auth'
import { menuUser, useSession } from '../../hooks/useSession'
import { usePersistedState } from '../../hooks/usePersistedState'

export function useLiveFocus(): [boolean, (v: boolean) => void] {
  const session = useSession()
  const [focus, setLocal] = usePersistedState<boolean>('live.focus', false)
  const account = menuUser(session)
  const saved = session.status === 'ready' ? session.me.preferences?.live_focus : undefined
  // The account's choice wins when it signs in; a device that has not been asked yet adopts it.
  useEffect(() => {
    if (account && saved !== undefined && saved !== null) setLocal(saved)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account?.id, saved])
  const set = (v: boolean) => {
    setLocal(v)
    if (account) void putPreferences({ live_focus: v }).catch(() => {})
  }
  return [focus, set]
}
