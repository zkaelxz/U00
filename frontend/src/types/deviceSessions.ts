// The signed-in-devices models in api/routers/auth_routes.py, from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type DeviceSession = Schemas['DeviceSession']
export type DeviceSessionList = Schemas['DeviceSessionList']
export type DeviceSessionsRevoked = Schemas['DeviceSessionsRevoked']
