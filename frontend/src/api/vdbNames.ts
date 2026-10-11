import type { TitleVdbNames, VdbNamesStatus } from '../types/vdbNames'
import { deleteJson, getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

// api/routers/vdb_names_routes.py. Download and delete are PC only.
export const getVdbNamesStatus = (f?: Fetch) => getJson<VdbNamesStatus>('/api/vdb-names', f)
export const downloadVdbNames = (f?: Fetch) => postJson<VdbNamesStatus>('/api/vdb-names/fetch', {}, pcOnlyFetch(f))
export const deleteVdbNames = (f?: Fetch) => deleteJson<VdbNamesStatus>('/api/vdb-names', pcOnlyFetch(f))
export const getTitleVdbNames = (dramaId: number, f?: Fetch) =>
  getJson<TitleVdbNames>(`/api/vdb-names/dramas/${dramaId}`, f)
export const setTitleVdbNames = (dramaId: number, enabled: boolean, f?: Fetch) =>
  postJson<TitleVdbNames>(`/api/vdb-names/dramas/${dramaId}`, { enabled }, f)
