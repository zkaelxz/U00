// Pure helpers for Settings > Signed-in devices (DevicesCard.tsx). The
// server decides everything (own sessions only, 404 for anything else, 409
// for this device); these only word it.
import type { SessionState } from '../../hooks/useSession'
import type { DeviceSession } from '../../types/deviceSessions'

/** Only a signed-in person has devices; the owner at the PC with sign-in off has none. */
export function showDevices(s: SessionState): boolean {
  return s.status === 'ready' && s.me.auth_enabled && s.me.signed_in && !!s.me.user && !s.me.user.is_local_owner
}

/** "203.0.113" -> "network 203.0.113.x"; an IPv6 /48 as is; "" -> "network unknown". */
export function networkText(prefix: string): string {
  if (!prefix) return 'network unknown'
  return /^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(prefix) ? `network ${prefix}.x` : `network ${prefix}`
}

/** How long ago an epoch-seconds time was, in plain words. */
export function agoText(epochSeconds: number, nowMs: number): string {
  const minutes = Math.floor((nowMs / 1000 - epochSeconds) / 60)
  if (minutes < 2) return 'just now'
  if (minutes < 60) return `${minutes} minutes ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return hours === 1 ? '1 hour ago' : `${hours} hours ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? 'yesterday' : `${days} days ago`
}

/** The accessible name used in a row's button labels ("Sign out Chrome on Android (network 203.0.113.x)"). */
export function deviceName(d: DeviceSession): string {
  return `${d.device} (${networkText(d.ip_prefix)})`
}

/** The muted line under a device's name. */
export function deviceDetails(d: DeviceSession, nowMs: number): string {
  const used = d.current ? 'In use now' : `Last used ${agoText(d.last_seen_at, nowMs)}`
  return [used, `signed in ${agoText(d.created_at, nowMs)}`, networkText(d.ip_prefix)].join(' · ')
}

export function otherDevices(list: DeviceSession[]): DeviceSession[] {
  return list.filter((d) => !d.current)
}

/** Why "Sign out all other devices" is off, or null when it can run. */
export function signOutOthersBlock(list: DeviceSession[]): string | null {
  return otherDevices(list).length === 0 ? 'No other devices are signed in.' : null
}

export function timeoutText(idleDays: number, maxDays: number): string {
  const days = (n: number) => (n === 1 ? '1 day' : `${n} days`)
  return `A device is signed out after ${days(idleDays)} without use, and ${days(maxDays)} after it signed in.`
}

/** The note after "Sign out all other devices". */
export function signedOutNote(n: number): string {
  if (n === 0) return 'No other devices were signed in.'
  return n === 1 ? 'Signed out 1 other device.' : `Signed out ${n} other devices.`
}
