// Owns which pages ask for the phone's bottom menu on a route where it is otherwise left out.
import { useEffect, useSyncExternalStore } from 'react'

let wanted = false
let owner: object | null = null
const listeners = new Set<() => void>()

const publish = (next: boolean) => {
  if (wanted === next) return
  wanted = next
  listeners.forEach((l) => l())
}

/** A page whose own bottom bar sits above the menu (the Simple check page) asks for it here, so the shell, which does
 *  not know the page's state, draws it. The ask ends with the page. */
export function useWantsPhoneBar(want: boolean) {
  useEffect(() => {
    if (!want) return
    // A remounted page's effect runs before the old one's cleanup, so a cleanup may only end the ask it made.
    const mine = {}
    owner = mine
    publish(true)
    return () => {
      if (owner !== mine) return
      owner = null
      publish(false)
    }
  }, [want])
}

export function usePhoneBarWanted() {
  return useSyncExternalStore((l) => { listeners.add(l); return () => { listeners.delete(l) } }, () => wanted, () => false)
}
