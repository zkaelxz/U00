// Mirrors the extension device-token models in api/schemas/system.py.

export type DeviceTokenStatus = 'active' | 'revoked' | 'expired'

export interface DeviceToken {
  // The row id, used only to revoke it (never the token).
  id: number
  label: string
  // Epoch seconds.
  created_at: number
  last_used_at: number | null
  // IPv4 /24 ("203.0.113") or IPv6 /48; "" if never used.
  last_used_ip_prefix: string
  // null: never expires.
  expires_at: number | null
  revoked_at: number | null
  status: DeviceTokenStatus
}

export interface DeviceTokenList {
  tokens: DeviceToken[]
  max_active: number
}

export interface DeviceTokenCreated {
  // Shown once; the server can't give it again.
  token: string
  device_token: DeviceToken
}

export interface DeviceTokenRevoked {
  revoked: number
}

export interface AdminDeviceToken extends DeviceToken {
  user_id: number
  // Display name, else a masked email.
  user_name: string
}

export interface AdminDeviceTokenList {
  tokens: AdminDeviceToken[]
}
