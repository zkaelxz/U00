import type { SourceConfig, SourceConfigUpdate } from '../types/workspace'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

export const getSourceConfig = (id: number, f?: Fetch) =>
  getJson<SourceConfig>(`/api/source/dramas/${id}/config`, f)

// Partial update: only the keys present are validated and written.
export const updateSourceConfig = (id: number, update: SourceConfigUpdate, f?: Fetch) =>
  postJson<SourceConfig>(`/api/source/dramas/${id}/config`, update, f)
