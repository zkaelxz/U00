// Owns which pages ask for the phone's bottom menu on a route where it is otherwise left out.
import { useEffect, useSyncExternalStore } from 'react'

let wanted = false
const listeners = new Set<() => void>()

const set = (next: boolean) => {
  if (wanted === next) return
  wanted = next
  listeners.forEach((l) => l())
}

/** A page whose own bottom bar sits above the menu (the Simple check page) asks for it here, so the shell, which does
 *  not know the page's state, draws it. The ask ends with the page. */
export function useWantsPhoneBar(want: boolean) {
  useEffect(() => {
    set(want)
    return () => set(false)
  }, [want])
}

export function usePhoneBarWanted() {
  return useSyncExternalStore((l) => { listeners.add(l); return () => { listeners.delete(l) } }, () => wanted, () => false)
}
