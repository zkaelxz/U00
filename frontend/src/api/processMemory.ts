// Diagnostics > Models > Memory by process (api/routers/loaded_models_routes.py). PC only.
import { getJson, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'
import type { LoadedModels } from './loadedModels'

export type ProcessRow = {
  pid: number
  parent_pid: number
  role: string
  relation: 'this_server' | 'child' | 'orphan' | 'other'
  ram_bytes: number
  vram_bytes: number | null
  command: string
}
export type ProcessMemory = {
  checked_at: string
  state: 'ok' | 'unsupported' | 'unavailable'
  server_pid: number
  processes: ProcessRow[]
  gpu: LoadedModels['gpu']
  vram_attributed_bytes: number | null
}

export const getProcessMemory = (f?: Fetch) =>
  getJson<ProcessMemory>('/api/settings/loaded-models/processes', pcOnlyFetch(f))
