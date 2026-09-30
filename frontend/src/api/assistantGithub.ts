// Deliver a maintenance-assistant proposed fix as a GitHub PR
// (api/routers/assistant_github_routes.py). Every route is PC only. The
// token is write-only: it goes in the body of one POST and no response
// carries it back. Token writes use a plain fetch (their 403 on the PC
// means key writes are off, like setEngineKey); the other writes go through
// pcOnlyFetch.
import type {
  GithubConnection,
  GithubDelivered,
  GithubPreview,
  GithubSettingsPatch,
  GithubStatus,
} from '../types/assistant'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/assistant/github'

export const getGithubStatus = (f?: Fetch) => getJson<GithubStatus>(BASE, f)

export const saveGithubSettings = (patch: GithubSettingsPatch, f?: Fetch) =>
  postJson<GithubStatus>(`${BASE}/settings`, patch, pcOnlyFetch(f))

export const setGithubToken = (value: string, f?: Fetch) =>
  postJson<{ token_configured: boolean }>(`${BASE}/token`, { value, confirm: true }, f)

export const clearGithubToken = (f?: Fetch) =>
  postJson<{ token_configured: boolean }>(`${BASE}/token/clear`, { confirm: true }, f)

export const testGithub = (f?: Fetch) => postJson<GithubConnection>(`${BASE}/test`, {}, pcOnlyFetch(f))

export const previewGithubPr = (patch: string, title: string, f?: Fetch) =>
  postJson<GithubPreview>(`${BASE}/preview`, { patch, title }, pcOnlyFetch(f))

export const deliverGithubPr = (patch: string, title: string, body: string, sha256: string, f?: Fetch) =>
  postJson<GithubDelivered>(`${BASE}/deliver`, { patch, title, body, sha256, confirm: true }, pcOnlyFetch(f))
