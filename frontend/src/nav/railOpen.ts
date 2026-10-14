/*
 * When the floating dock shows its labelled rows. CSS :hover and :focus-within could not express this: a mouse click on a
 * link or toggle leaves focus inside, so the dock stayed open after the pointer left until the person clicked the page.
 * Pointer leave closes after a short delay unless keyboard focus is inside; focus from a mouse click does not count.
 */
export const RAIL_CLOSE_DELAY_MS = 300

export interface RailOpenController {
  pointerEnter(): void
  pointerLeave(): void
  /** `keyboard` is whether the focused element matches :focus-visible. */
  focusIn(keyboard: boolean): void
  focusOut(): void
  /** A press outside the dock (tapping the page on touch). */
  pressOutside(): void
  dispose(): void
}

export function createRailOpen(setOpen: (open: boolean) => void): RailOpenController {
  let pointerInside = false
  let keyboardFocus = false
  let timer: ReturnType<typeof setTimeout> | null = null

  const clear = () => {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }
  const sync = () => setOpen(pointerInside || keyboardFocus)

  return {
    pointerEnter() {
      clear()
      pointerInside = true
      sync()
    },
    pointerLeave() {
      pointerInside = false
      clear()
      if (keyboardFocus) return
      timer = setTimeout(() => {
        timer = null
        sync()
      }, RAIL_CLOSE_DELAY_MS)
    },
    focusIn(keyboard) {
      if (!keyboard) return
      clear()
      keyboardFocus = true
      sync()
    },
    focusOut() {
      keyboardFocus = false
      if (!pointerInside) sync()
    },
    pressOutside() {
      clear()
      pointerInside = false
      keyboardFocus = false
      sync()
    },
    dispose: clear,
  }
}
