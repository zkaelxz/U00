/*
 * The PC window's Owner/Guest profile (GET/POST/POST /api/pc-profile*, PC-only).
 * With a PIN set the PC opens on Guest, which leaves private titles out of
 * every list; the Owner profile needs the PIN. This hides titles from view;
 * it is not a lock on the data.
 */
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'
import { browserSession } from './sessionStore'

export type PcProfileStatus = { pin_set: boolean; guest: boolean }

const PICKED_KEY = 'baihe.pcProfilePicked'

export const getPcProfile = () => getJson<PcProfileStatus>('/api/pc-profile')
export const unlockPcProfile = (pin: string) =>
  postJson<PcProfileStatus>('/api/pc-profile/unlock', { pin }, pcOnlyFetch())
export const lockPcProfile = () => postJson<PcProfileStatus>('/api/pc-profile/lock', undefined, pcOnlyFetch())
export const clearPcProfilePin = (pin: string) =>
  postJson<PcProfileStatus>('/api/pc-profile/clear', { pin }, pcOnlyFetch())
export const setPcProfilePin = (newPin: string, currentPin?: string, hideExisting = false) =>
  postJson<PcProfileStatus & { hidden: number }>(
    '/api/pc-profile/pin',
    { new_pin: newPin, current_pin: currentPin ?? null, hide_existing: hideExisting },
    pcOnlyFetch(),
  )

/** Whether this tab already chose a profile; a new tab asks again. */
export function profilePicked(): boolean {
  try {
    return browserSession()?.getItem(PICKED_KEY) === '1'
  } catch {
    return false
  }
}

export function rememberProfilePicked(picked: boolean): void {
  try {
    if (picked) browserSession()?.setItem(PICKED_KEY, '1')
    else browserSession()?.removeItem(PICKED_KEY)
  } catch {
    // Storage blocked: the picker just shows again on reload.
  }
}

export function validPin(pin: string): boolean {
  return /^[0-9]{6,12}$/.test(pin)
}
export const PIN_HINT = 'Use 6 to 12 digits.'
