// The opt-in real-model check (api/routers/real_model_check_routes.py).
// Starting it is PC only (pcOnlyFetch); the status read is admin.diagnostics
// and is polled while the job runs.
import type { DiagnosticsJobStarted } from '../types/diagnosticsInstalls'
import type { RealModelCheckState } from '../types/realModelCheck'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const URL = '/api/diagnostics/real-model-check'

export const getRealModelCheck = (f?: Fetch) => getJson<RealModelCheckState>(URL, f)

export const startRealModelCheck = (f?: Fetch) =>
  postJson<DiagnosticsJobStarted>(URL, { confirm: true }, pcOnlyFetch(f))
