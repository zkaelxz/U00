// Settings > Loaded now (api/routers/loaded_models_routes.py). The read is
// open to anyone who can read Settings; freeing the app's models is PC only.
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export type OllamaLoaded = {
  state: 'running' | 'not_running' | 'not_local' | 'unavailable'
  models: { name: string; size_bytes: number; vram_bytes: number }[]
}
export type AppLoaded = {
  state: 'ok' | 'unavailable'
  models: { name: string; kind: string; device: 'GPU' | 'CPU' | 'Unknown' }[]
}
export type GpuMemory = {
  state: 'ok' | 'unknown'
  name?: string | null
  total_bytes?: number | null
  used_bytes?: number | null
  free_bytes?: number | null
}
export type KeepFreeMemory = {
  state: 'ok' | 'unknown'
  total_bytes: number | null
  free_bytes: number | null
  reserved_bytes: number
}
export type LoadedModels = {
  checked_at: string
  ollama: OllamaLoaded
  app: AppLoaded
  gpu: GpuMemory
  memory: { vram: KeepFreeMemory; ram: KeepFreeMemory }
  llama_cpp_running: boolean
  gpu_job_running: boolean
}

const BASE = '/api/settings/loaded-models'

export const getLoadedModels = (f?: Fetch) => getJson<LoadedModels>(BASE, f)
export const freeAppModels = (f?: Fetch) =>
  postJson<LoadedModels>(`${BASE}/free-app-models`, { confirm: true }, pcOnlyFetch(f))
