// Installs that wait for the next start (api/routers/pending_install_routes.py).
// All PC only. Only registry package keys are sent; the server derives the pip
// command, so nothing here can carry an argument, a URL or a version.
import type { PendingInstallPlan, PendingInstallQueued, PendingInstallStatus } from '../types/pendingInstall'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/diagnostics/pending-install'

export const getPendingInstall = (f?: Fetch) => getJson<PendingInstallStatus>(BASE, f)

// Runs `pip install --dry-run` on the server: can take a while online.
export const planInstall = (packages: string[], f?: Fetch) =>
  postJson<PendingInstallPlan>(`${BASE}/plan`, { packages }, pcOnlyFetch(f))

// acceptRisk: the owner ticked "I understand" for a downgrade of something Baihe needs.
export const queueInstall = (packages: string[], acceptRisk: boolean, f?: Fetch) =>
  postJson<PendingInstallQueued>(`${BASE}/queue`, { packages, confirm: true, accept_risk: acceptRisk }, pcOnlyFetch(f))

export const cancelPendingInstall = (f?: Fetch) =>
  postJson<{ cancelled: boolean }>(`${BASE}/cancel`, {}, pcOnlyFetch(f))

export const dismissInstallResult = (f?: Fetch) =>
  postJson<{ dismissed: boolean }>(`${BASE}/dismiss`, {}, pcOnlyFetch(f))
