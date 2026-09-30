// Mirrors the signed-in-devices models in api/routers/auth_routes.py.

export interface DeviceSession {
  // The session's row id, used only to sign that device out (never its token).
  id: number
  // A coarse label such as "Chrome on Android"; never the full user agent.
  device: string
  // Epoch seconds.
  created_at: number
  last_seen_at: number
  expires_at: number
  // IPv4 /24 ("203.0.113") or IPv6 /48 ("2001:db8:1::/48"); "" if unknown.
  ip_prefix: string
  // The device making the request.
  current: boolean
}

export interface DeviceSessionList {
  sessions: DeviceSession[]
  idle_timeout_days: number
  absolute_timeout_days: number
}

export interface DeviceSessionsRevoked {
  revoked: number
}
