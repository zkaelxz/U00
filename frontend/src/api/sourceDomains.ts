// Per-source domain lists. All routes are local_only, so every call goes
// through pcOnlyFetch (X-Baihe-Local). Reset has no fields but the cross-site
// gate needs the header and a JSON content type, so it sends an empty object.
import type { SourceDomainDismissed, SourceDomainEntry, SourceDomainProposal } from '../types/sourceDomains'
import { getJson, postJson, type Fetch, hasStatus } from './client'
import { pcOnlyFetch } from './pcOnly'

const BASE = '/api/source-domains'
const seg = (s: string) => encodeURIComponent(s)

export const listSourceDomains = (f?: Fetch) => getJson<SourceDomainEntry[]>(BASE, f)
export const listDomainProposals = (f?: Fetch) => getJson<SourceDomainProposal[]>(`${BASE}/proposals`, f)

export const saveSourceDomains = (source: string, domains: string[], f?: Fetch) =>
  postJson<SourceDomainEntry>(`${BASE}/${seg(source)}`, { domains }, pcOnlyFetch(f))
export const resetSourceDomains = (source: string, f?: Fetch) =>
  postJson<SourceDomainEntry>(`${BASE}/${seg(source)}/reset`, {}, pcOnlyFetch(f))
export const confirmDomainProposal = (source: string, host: string, f?: Fetch) =>
  postJson<SourceDomainEntry>(`${BASE}/proposals/confirm`, { source, host }, pcOnlyFetch(f))
export const dismissDomainProposal = (source: string, host: string, f?: Fetch) =>
  postJson<SourceDomainDismissed>(`${BASE}/proposals/dismiss`, { source, host }, pcOnlyFetch(f))

/** True when the server does not have these routes (an older build). */
export const isMissingRoute = (e: unknown) => hasStatus(e, 404)
