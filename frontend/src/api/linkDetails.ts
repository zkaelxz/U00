/*
 * "Fill from link" (api/routers/link_details_routes.py). Every route is PC
 * only: each read fetches from the internet on the PC's connection.
 */
import { getJson, postJson, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

// api/schemas/link_details.py MediaLinkProbe. kind "page": nothing was read.
export interface MediaLinkProbe {
  kind: 'video' | 'source' | 'page'
  title: string
  channel: string
  release_date: string // '' = the link gives none
  is_live: boolean
  summary: string
  platform: string
  series_suggestion: string
  note: string
  display_url: string
}

export interface LinkDetailFields {
  title_zh: string
  author: string
  release_date: string
  series: string
}

interface TitleLinkProbe {
  drama_id: number
  link: MediaLinkProbe
  current: LinkDetailFields
  suggested: LinkDetailFields
}

export interface LinkScanRow {
  drama_id: number
  name: string
  display_url: string
  blank: string[]
  current: LinkDetailFields | null
  suggested: LinkDetailFields | null
  note: string
}

interface LinkScanResult {
  job_id: string
  status: string
  total: number
  titles: LinkScanRow[]
}

export const LINK_SCAN_JOB_ID = 'link_scan'

export const probeLink = (url: string, f?: Fetch) =>
  postJson<MediaLinkProbe>('/api/media-link/probe', { url }, pcOnlyFetch(f))

// Reads the title's own stored link (the full one, which only the PC holds).
export const probeTitleLink = (dramaId: number, f?: Fetch) =>
  postJson<TitleLinkProbe>(`/api/dramas/${dramaId}/link-details/probe`, {}, pcOnlyFetch(f))

// Each field is written only while it is still blank on the server.
export const applyLinkDetails = (dramaId: number, fields: Partial<LinkDetailFields>, f?: Fetch) =>
  postJson<{ applied: string[]; kept: string[] }>(`/api/dramas/${dramaId}/link-details/apply`, fields, pcOnlyFetch(f))

export const startLinkScan = (f?: Fetch) =>
  postJson<{ job_id: string; total: number }>('/api/library/link-scan', {}, pcOnlyFetch(f))

export const getLinkScan = (f?: Fetch) => getJson<LinkScanResult>('/api/library/link-scan', pcOnlyFetch(f))
