/*
 * Engine and model picked for the viewer (owner decision 2026-11-24): a member's always, and anyone's on another
 * device until an admin there presses Show options. The page hides its pickers while `auto` is true and sends
 * no engine, so the server chooses one the account may use.
 */
import { useState } from 'react'

import { usePcOnly } from './usePcOnly'
import { pickedForYou, useSession } from './useSession'

export type ChosenForYou = { auto: boolean; canShow: boolean; show: () => void }

export function useChosenForYou(): ChosenForYou {
  const picked = pickedForYou(useSession(), usePcOnly())
  const [showOptions, setShowOptions] = useState(false)
  return { auto: picked === 'member' || (picked === 'away' && !showOptions), canShow: picked === 'away', show: () => setShowOptions(true) }
}
