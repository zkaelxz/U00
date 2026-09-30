// Model health (Step 40; api/routers/model_registry_routes.py,
// api/model_registry_schemas.py): is a model this app is set up to use
// deprecated, retired or no longer listed by its provider? The status read
// needs admin.diagnostics and never calls out. The provider check and the
// preset switch are PC only and go through pcOnlyFetch (X-Baihe-Local; a 403
// marks the tab remote). Nothing is ever switched automatically, and no key
// is ever returned.
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/models'

export type ModelKind = 'default' | 'tier' | 'preset' | 'extension'
export type ModelStatusValue = 'retired' | 'deprecated' | 'not_listed' | 'not_offered' | 'legacy' | 'current' | 'unknown'

export interface ModelStatusItem {
  engine: string
  model: string
  kind: ModelKind
  // Where the model is set, e.g. "Preset: Drama A" or "claude built-in default".
  where: string
  preset_id?: number | null
  status: ModelStatusValue
  message: string
  replacement?: string | null
  note?: string | null
  // true/false after a provider check; null when that engine wasn't checked.
  listed_by_provider?: boolean | null
  // 0 (current/unknown) to 3 (retired/not listed).
  severity: number
  can_switch: boolean
}

export interface EngineCheck {
  ok: boolean
  model_count: number
  error?: string | null
}

export interface ModelStatus {
  items: ModelStatusItem[]
  // Items with severity 2 or more.
  warnings: number
  checked_at: string | null
  engines_checked: Record<string, EngineCheck>
  registry_updated: string | null
}

export interface PresetModelSwitchResult {
  preset_id: number
  engine: string | null
  from_model: string
  to_model: string
}

// Cached data only: the registry shipped with the app and the last manual check.
export const getModelStatus = (f?: Fetch) => getJson<ModelStatus>(`${BASE}/status`, f)

// PC only: asks each configured provider for its model list (429 within a minute of the last check).
export const checkModelProviders = (f?: Fetch) => postJson<ModelStatus>(`${BASE}/check`, undefined, pcOnlyFetch(f))

// PC only: the user-confirmed switch of one preset; 409 when the preset's model changed since it was read.
export const switchPresetModel = (presetId: number, fromModel: string, toModel: string, f?: Fetch) =>
  postJson<PresetModelSwitchResult>(
    `${BASE}/presets/${presetId}/switch`,
    { from_model: fromModel, to_model: toModel, confirm: true },
    pcOnlyFetch(f),
  )
