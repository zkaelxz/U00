import { getJson, postJson, type Fetch } from './client'
import type { components } from './generated/schema'
import { pcOnlyFetch } from './pcOnly'

// The watch-folder inbox (api/routers/inbox_routes.py). Both calls are PC only.
export type InboxState = components['schemas']['InboxState']
export type InboxUpdate = components['schemas']['InboxUpdate']

export const getInbox = (f?: Fetch) => getJson<InboxState>('/api/inbox', f)
export const setInbox = (body: InboxUpdate, f?: Fetch) => postJson<InboxState>('/api/inbox', body, pcOnlyFetch(f))
