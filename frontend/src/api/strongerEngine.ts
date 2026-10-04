// Suggest a stronger engine for a hard line in Review
// (api/routers/stronger_engine_routes.py). Suggest only: the GET makes no
// engine call, and the POST makes one (possibly paid) call and writes nothing.
import type { StrongerEngineSuggestions, StrongerLineResult } from '../types/strongerEngine'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

const BASE = '/api/stronger-engine/dramas'

/** Which lines to suggest it for, why, and the per-line estimate. Cheap. */
export const getStrongerSuggestions = (dramaId: number, f?: Fetch) =>
  getJson<StrongerEngineSuggestions>(`${BASE}/${dramaId}`, f)

/** One line with the stronger engine. The body names no engine, model or key. */
export const tryStrongerEngine = (dramaId: number, lineId: number, f?: Fetch) =>
  postJson<StrongerLineResult>(`${BASE}/${dramaId}/lines/${lineId}/try`, {}, f)
