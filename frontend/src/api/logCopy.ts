import { getJson, putJson, type Fetch } from './client'
import type { components } from './generated/schema'
import { pcOnlyFetch } from './pcOnly'

// The weekly scrubbed log copy (api/routers/diagnostics_gaps_routes.py). Both calls are PC only.
export type LogCopyState = components['schemas']['LogCopySettings']

export const getLogCopy = (f?: Fetch) => getJson<LogCopyState>('/api/diagnostics/log-copy', f)
export const setLogCopyFolder = (folder: string, f?: Fetch) =>
  putJson<LogCopyState>('/api/diagnostics/log-copy', { folder }, pcOnlyFetch(f))
