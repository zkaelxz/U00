// Browser-extension bridge (api/routers/extension_routes.py). The bridge
// routes are PC only, so they go through pcOnlyFetch (a 403 marks the tab
// remote); reading the engine is admin.settings, so it is a plain GET.
// The token is handed to the caller only; nothing here stores or logs it.
import type {
  ExtensionEnabledResult, ExtensionEngineSettings, ExtensionStatus, ExtensionToken,
} from '../types/extension'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/extension'

export const getExtensionStatus = (f?: Fetch) => getJson<ExtensionStatus>(`${BASE}/status`, pcOnlyFetch(f))

export const setExtensionEnabled = (enabled: boolean, f?: Fetch) =>
  postJson<ExtensionEnabledResult>(`${BASE}/enabled`, { enabled }, pcOnlyFetch(f))

export const revealExtensionToken = (f?: Fetch) =>
  postJson<ExtensionToken>(`${BASE}/token`, { confirm: true }, pcOnlyFetch(f))

export const getExtensionEngine = (f?: Fetch) => getJson<ExtensionEngineSettings>(`${BASE}/engine`, f)

/** engine null = no translation (original text only). */
export const setExtensionEngine = (engine: string | null, model: string | null, f?: Fetch) =>
  postJson<ExtensionEngineSettings>(`${BASE}/engine`, { engine, model }, pcOnlyFetch(f))
