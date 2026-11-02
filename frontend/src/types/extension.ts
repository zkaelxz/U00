// Mirrors api/schemas/extension_access.py Extension* (/api/extension, PC only), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type ExtensionStatus = Schemas['ExtensionStatus']
export type ExtensionEnabledResult = Schemas['ExtensionEnabledResult']
export type ExtensionToken = Schemas['ExtensionToken']

// engine and model are generated as optional; callers read them as `string | null`.
export type ExtensionEngineSettings = Omit<Schemas['ExtensionEngineSettings'], 'engine' | 'model'> & {
  engine: string | null
  model: string | null
}
