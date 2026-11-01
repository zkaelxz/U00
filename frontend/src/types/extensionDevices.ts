// The extension device-token models in api/schemas/extension_access.py, from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type DeviceToken = Schemas['DeviceToken']
export type DeviceTokenList = Schemas['DeviceTokenList']
export type DeviceTokenCreated = Schemas['DeviceTokenCreated']
export type DeviceTokenRevoked = Schemas['DeviceTokenRevoked']
export type AdminDeviceToken = Schemas['AdminDeviceToken']
export type AdminDeviceTokenList = Schemas['AdminDeviceTokenList']
