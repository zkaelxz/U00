// Browser-extension device tokens (api/routers/device_token_routes.py). The
// own routes act on the signed-in person's tokens only (the server takes the
// user from the session); the admin routes list and revoke anyone's. Writes
// carry the CSRF header through postJson like every other write.
import type {
  AdminDeviceTokenList, DeviceTokenCreated, DeviceTokenList, DeviceTokenRevoked,
} from '../types/extensionDevices'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

const OWN = '/api/auth/device-tokens'
const ADMIN = '/api/admin/device-tokens'

export const listMyDeviceTokens = (f?: Fetch) => getJson<DeviceTokenList>(OWN, f)

export const createDeviceToken = (label: string, expiresInDays: number | null, f?: Fetch) =>
  postJson<DeviceTokenCreated>(OWN, { label, expires_in_days: expiresInDays }, f)

export const revokeMyDeviceToken = (id: number, f?: Fetch) =>
  postJson<DeviceTokenRevoked>(`${OWN}/${id}/revoke`, undefined, f)

export const listAllDeviceTokens = (f?: Fetch) => getJson<AdminDeviceTokenList>(ADMIN, f)

export const revokeAnyDeviceToken = (id: number, f?: Fetch) =>
  postJson<DeviceTokenRevoked>(`${ADMIN}/${id}/revoke`, undefined, f)
