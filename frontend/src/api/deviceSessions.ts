// The signed-in person's own devices (api/routers/auth_routes.py). Any
// signed-in user may call these for their own sessions only; the server takes
// the user from the session cookie, never from the request. Writes carry the
// CSRF header through postJson like every other write.
import type { DeviceSessionList, DeviceSessionsRevoked } from '../types/deviceSessions'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

const BASE = '/api/auth/sessions'

export const listDeviceSessions = (f?: Fetch) => getJson<DeviceSessionList>(BASE, f)

export const signOutDevice = (id: number, f?: Fetch) =>
  postJson<DeviceSessionsRevoked>(`${BASE}/${id}/revoke`, undefined, f)

export const signOutOtherDevices = (f?: Fetch) =>
  postJson<DeviceSessionsRevoked>(`${BASE}/revoke-others`, undefined, f)
