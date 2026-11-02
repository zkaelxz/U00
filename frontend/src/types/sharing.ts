// Who can see each drama and series (api/sharing_schemas.py), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type SharingItem = Schemas['SharingItem']
export type SharingList = Schemas['SharingList']
export type SetPrivateResult = Schemas['SetPrivateResult']
export type ShareByDefault = Schemas['ShareByDefault']
export type SharingKind = SharingItem['kind']
