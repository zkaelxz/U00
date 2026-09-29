// Browser-extension bridge (api/routers/extension_routes.py). Every route is
// PC only, so all three go through pcOnlyFetch (a 403 marks the tab remote).
// The token is handed to the caller only; nothing here stores or logs it.
import type { ExtensionEnabledResult, ExtensionStatus, ExtensionToken } from '../types/extension'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/extension'

export const getExtensionStatus = (f?: Fetch) => getJson<ExtensionStatus>(`${BASE}/status`, pcOnlyFetch(f))

export const setExtensionEnabled = (enabled: boolean, f?: Fetch) =>
  postJson<ExtensionEnabledResult>(`${BASE}/enabled`, { enabled }, pcOnlyFetch(f))

export const revealExtensionToken = (f?: Fetch) =>
  postJson<ExtensionToken>(`${BASE}/token`, { confirm: true }, pcOnlyFetch(f))
