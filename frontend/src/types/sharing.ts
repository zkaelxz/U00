// Who can see each drama and series (api/schemas/sharing.py), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type SharingItem = Schemas['SharingItem']
export type SharingList = Schemas['SharingList']
export type SetPrivateResult = Schemas['SetPrivateResult']
export type SetOwnerResult = Schemas['SetOwnerResult']
export type ShareByDefault = Schemas['ShareByDefault']
export type SharingKind = SharingItem['kind']
