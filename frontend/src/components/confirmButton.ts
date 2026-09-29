// Pure helpers for ConfirmButton (kept out of the component file for fast refresh).

// Idle time after which an armed button reverts to its first step.
export const CONFIRM_REVERT_MS = 5000

export type ConfirmEvent = 'press' | 'cancel' | 'timeout' | 'blur' | 'escape' | 'done'

/**
 * Next armed state and whether this event runs the action. The first press
 * only arms (no API call); a press while armed runs it; anything else disarms.
 */
export function confirmStep(armed: boolean, event: ConfirmEvent): { armed: boolean; run: boolean } {
  if (event === 'press') return armed ? { armed: false, run: true } : { armed: true, run: false }
  return { armed: false, run: false }
}

export const confirmLabelFor = (name: string) => `Confirm delete ${name}`
export const armedAnnouncement = (name: string) => `Press again to delete ${name}`
