// The optional Jellyfin connector (api/routers/jellyfin_routes.py). Every
// route is PC only, so calls go through pcOnlyFetch (a 403 marks the tab
// remote) -- except the key set/clear, which also sit behind the key-write
// gate: its 403 on the PC just means key writes are off, so, like
// setEngineKey, they use a plain fetch. The key goes in the body only and
// never comes back.
import type {
  JellyfinConfig, JellyfinLanguage, JellyfinScanReport, JellyfinSendRequest, JellyfinSendResult,
} from '../types/jellyfin'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/jellyfin'

export const getJellyfinConfig = (f?: Fetch) => getJson<JellyfinConfig>(`${BASE}/config`, pcOnlyFetch(f))

export const saveJellyfinConfig = (
  body: { enabled?: boolean; server_url?: string; library_dir?: string }, f?: Fetch,
) => postJson<JellyfinConfig>(`${BASE}/config`, body, pcOnlyFetch(f))

export const setJellyfinKey = (value: string, f?: Fetch) =>
  postJson<JellyfinConfig>(`${BASE}/key`, { value, confirm: true }, f)

export const clearJellyfinKey = (f?: Fetch) => postJson<JellyfinConfig>(`${BASE}/key/clear`, { confirm: true }, f)

export const testJellyfin = (f?: Fetch) =>
  postJson<{ ok: boolean; server_name: string; version: string }>(`${BASE}/test`, {}, pcOnlyFetch(f))

export const scanJellyfin = (language: JellyfinLanguage, f?: Fetch) =>
  postJson<JellyfinScanReport>(`${BASE}/scan`, { language }, pcOnlyFetch(f))

export const sendToJellyfin = (dramaId: number, body: JellyfinSendRequest, f?: Fetch) =>
  postJson<JellyfinSendResult>(`${BASE}/dramas/${dramaId}/send`, body, pcOnlyFetch(f))
