// Notion export (api/routers/notion_routes.py, roadmap item 112). Every
// route is PC only, so calls go through pcOnlyFetch (a 403 marks the tab
// remote) -- except the token set/clear, which also sit behind the key-write
// gate: its 403 on the PC just means key writes are off, so, like
// setJellyfinKey, they use a plain fetch. The token goes in the body only
// and never comes back.
import type { NotionConfig, NotionDramaPage, NotionField, NotionTargetType, NotionTestResult } from '../types/notion'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/notion'

export const getNotionConfig = (f?: Fetch) => getJson<NotionConfig>(`${BASE}/config`, pcOnlyFetch(f))

// target_id takes a Notion link or id; "" clears it. Send only what changed.
export const saveNotionConfig = (body: { target_type?: NotionTargetType; target_id?: string }, f?: Fetch) =>
  postJson<NotionConfig>(`${BASE}/config`, body, pcOnlyFetch(f))

export const setNotionToken = (value: string, f?: Fetch) =>
  postJson<NotionConfig>(`${BASE}/token`, { value, confirm: true }, f)

export const clearNotionToken = (f?: Fetch) => postJson<NotionConfig>(`${BASE}/token/clear`, { confirm: true }, f)

export const testNotion = (f?: Fetch) => postJson<NotionTestResult>(`${BASE}/test`, {}, pcOnlyFetch(f))

export const getNotionDramaPage = (dramaId: number, f?: Fetch) =>
  getJson<NotionDramaPage>(`${BASE}/dramas/${dramaId}`, pcOnlyFetch(f))

export const startNotionExport = (dramaId: number, field: NotionField, f?: Fetch) =>
  postJson<{ job_id: string }>(`${BASE}/dramas/${dramaId}/export`, { field }, pcOnlyFetch(f))
