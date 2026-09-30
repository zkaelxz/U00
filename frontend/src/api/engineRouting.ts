// Settings > "Which engine does what" (Step 36, api/routers/engine_routing_routes.py).
// Reading is admin.settings; choosing an engine and Test are PC only, so the
// writes go through pcOnlyFetch (a 403 marks the tab remote).
import type { CapabilityRoute, EngineRouteStatus, EngineRouting } from '../types/engineRouting'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/settings/engine-routing'

export const getEngineRouting = (f?: Fetch) => getJson<EngineRouting>(BASE, f)

/** engine null puts the task back on its default engine. */
export const setCapabilityEngine = (capability: string, engine: string | null, f?: Fetch) =>
  postJson<CapabilityRoute>(`${BASE}/capabilities/${encodeURIComponent(capability)}`, { engine }, pcOnlyFetch(f))

/** One short real call with the key saved on the PC (can take up to ~45 s). */
export const testEngine = (engine: string, f?: Fetch) =>
  postJson<EngineRouteStatus>(`${BASE}/engines/${encodeURIComponent(engine)}/test`, {}, pcOnlyFetch(f))
