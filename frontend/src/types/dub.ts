// Mirrors api/schemas/voice.py NarrationConfig / NarrationRunRequest (the
// chunk-and-tag step of a narrated novel). No paths or secrets exist here.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type NarrationConfig = Schemas['NarrationConfig']

export interface NarrationRunRequest {
  engine?: string
  model?: string
}
