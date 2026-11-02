// Reader Listen (api/schemas/reader.py ListenConfig), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type ListenTtsEngine = Schemas['DubTtsEngine']
export type ListenConfig = Schemas['ListenConfig']

export interface ListenRunRequest {
  tts_engine: string
  narration_language?: string
}
