// App updates (api/routers/update_routes.py). Every route is PC only, so all
// go through pcOnlyFetch (a 403 marks the tab as away from the PC).
import type { UpdateInstallResponse, UpdateStatus } from '../types/update'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/system/update'

export const getUpdateStatus = (f?: Fetch) => getJson<UpdateStatus>(BASE, pcOnlyFetch(f))

export const checkForUpdates = (f?: Fetch) => postJson<UpdateStatus>(`${BASE}/check`, {}, pcOnlyFetch(f))

export const setUpdateAutoCheck = (autoCheck: boolean, f?: Fetch) =>
  postJson<UpdateStatus>(`${BASE}/settings`, { auto_check: autoCheck }, pcOnlyFetch(f))

export const downloadUpdate = (f?: Fetch) => postJson<UpdateStatus>(`${BASE}/download`, {}, pcOnlyFetch(f))

export const installUpdate = (f?: Fetch) =>
  postJson<UpdateInstallResponse>(`${BASE}/install`, { confirm: true }, pcOnlyFetch(f))
