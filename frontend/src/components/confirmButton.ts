// Pure helpers for ConfirmButton (kept out of the component file for fast refresh).

// Idle time after which an armed button reverts to its first step.
export const CONFIRM_REVERT_MS = 5000

// 'blocked': the button became busy or disabled; it always disarms, so a
// later re-enable starts again from the first step.
export type ConfirmEvent = 'press' | 'cancel' | 'timeout' | 'blur' | 'escape' | 'blocked'

/**
 * Next armed state and whether this event runs the action. The first press
 * only arms (no API call); a press while armed runs it; anything else disarms.
 * A press while blocked does nothing.
 */
export function confirmStep(
  armed: boolean,
  event: ConfirmEvent,
  blocked = false,
): { armed: boolean; run: boolean } {
  if (blocked) return { armed: false, run: false }
  if (event === 'press') return armed ? { armed: false, run: true } : { armed: true, run: false }
  return { armed: false, run: false }
}

/** Replay events from the first step; how many times the action ran. For tests and reasoning. */
export function replay(events: { event: ConfirmEvent; blocked?: boolean }[]): number {
  let armed = false
  let runs = 0
  for (const { event, blocked } of events) {
    const next = confirmStep(armed, event, blocked)
    armed = next.armed
    if (next.run) runs += 1
  }
  return runs
}

export const confirmLabelFor = (name: string) => `Confirm delete ${name}`
export const armedAnnouncement = (name: string) => `Press again to delete ${name}`
