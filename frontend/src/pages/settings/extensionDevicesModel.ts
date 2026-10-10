// Pure helpers for Settings > Browser extension devices
// (ExtensionDevicesCard.tsx). The server decides everything (own tokens
// only, 404 for anything else, 403 without extension.send or for an admin
// account away from the PC); these only decide what to show and word it.
import type { PcMode } from '../../hooks/usePcOnly'
import type { SessionState } from '../../hooks/useSession'
import type { DeviceToken } from '../../types/extensionDevices'
import { agoText, networkText } from './deviceSessionsModel'

// Mirrors MAX_LABEL_CHARS in services/device_token_service.py.
export const MAX_DEVICE_LABEL_CHARS = 40

export const EXPIRY_CHOICES: { days: number | null; label: string }[] = [
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 365, label: '1 year' },
  { days: null, label: 'Never' },
]
export const DEFAULT_EXPIRY_DAYS = 90

export const ADMIN_TOKENS_PC_ONLY =
  "An admin account's extension devices can only be added on the main PC. You can revoke one from anywhere."
export const NO_SEND_PERMISSION =
  'Adding a device needs permission to use the browser extension (extension.send). Ask whoever runs Baihe on the main PC.'

export interface ExtensionDevicesView {
  // The signed-in person's own tokens.
  own: boolean
  canCreate: boolean
  // Everyone's tokens: PC only (the server's local_only()).
  all: boolean
}

/** Own tokens for a signed-in person (sign-in on); everyone's for the owner or
 * an admin at the main PC. Nothing otherwise. */
export function extensionDevicesView(s: SessionState, pc: PcMode): ExtensionDevicesView {
  if (s.status !== 'ready') return { own: false, canCreate: false, all: false }
  const me = s.me
  const own = me.auth_enabled && me.signed_in && !!me.user && !me.user.is_local_owner
  return {
    own,
    canCreate: own && me.permissions.includes('extension.send'),
    all: pc === 'local' && !!me.user && (me.user.is_local_owner || me.user.is_admin),
  }
}

/** Why an admin account can't add or revoke its own here (server: 403 away
 * from the PC), or null. 'unknown' waits for 'local' like the other admin blocks. */
export function adminTokensBlock(s: SessionState, pc: PcMode): string | null {
  const admin = s.status === 'ready' && !!s.me.user?.is_admin
  return admin && pc !== 'local' ? ADMIN_TOKENS_PC_ONLY : null
}

/** The trimmed label, or why it can't be used. */
export function labelProblem(label: string): string | null {
  const text = label.trim().replace(/\s+/g, ' ')
  if (!text) return 'Name the device, for example "Work laptop".'
  if (text.length > MAX_DEVICE_LABEL_CHARS) return `Use at most ${MAX_DEVICE_LABEL_CHARS} characters.`
  return null
}

function inText(epochSeconds: number, nowMs: number): string {
  const days = Math.ceil((epochSeconds - nowMs / 1000) / 86400)
  return days <= 1 ? 'within a day' : `in ${days} days`
}

/** The muted line under a device's name. */
export function tokenDetails(t: DeviceToken, nowMs: number): string {
  if (t.status === 'revoked' && t.revoked_at !== null) return `Revoked ${agoText(t.revoked_at, nowMs)}`
  if (t.status === 'expired') return 'Expired'
  const used = t.last_used_at === null
    ? 'Never used'
    : `Last used ${agoText(t.last_used_at, nowMs)} · ${networkText(t.last_used_ip_prefix)}`
  const expires = t.expires_at === null ? 'never expires' : `expires ${inText(t.expires_at, nowMs)}`
  return [used, `added ${agoText(t.created_at, nowMs)}`, expires].join(' · ')
}

export function activeCount(list: DeviceToken[]): number {
  return list.filter((t) => t.status === 'active').length
}

export const SHOWN_ONCE_WARNING =
  "Copy this token into the extension's options now. It is shown only once: Baihe keeps only a fingerprint of it. Lost it? Revoke this device and add it again."
