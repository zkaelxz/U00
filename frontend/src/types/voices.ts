// Mirrors the Voice* models in api/schemas/voices.py.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type VoiceSample = Schemas['VoiceSample']
export type VoiceEntry = Schemas['VoiceEntry']
export type VoiceListResponse = Schemas['VoiceListResponse']
export type VoiceChangeResult = Schemas['VoiceChangeResult']
