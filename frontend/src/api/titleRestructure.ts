import type {
  TitleRestructureInfo,
  TitleRestructurePreview,
  TitleRestructureRequest,
  TitleRestructureResult,
} from '../types/titleRestructure'
import { getJson, postJson, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

// api/routers/drama_routes.py: all three are PC only (they move files on disk).
export const getRestructure = (dramaId: number, f?: Fetch) =>
  getJson<TitleRestructureInfo>(`/api/dramas/${dramaId}/restructure`, pcOnlyFetch(f))

// Writes nothing; the fingerprint goes back with the confirmed change.
export const previewRestructure = (dramaId: number, body: TitleRestructureRequest, f?: Fetch) =>
  postJson<TitleRestructurePreview>(`/api/dramas/${dramaId}/restructure/preview`, body, pcOnlyFetch(f))

export const applyRestructure = (dramaId: number, body: TitleRestructureRequest, fingerprint: string, f?: Fetch) =>
  postJson<TitleRestructureResult>(
    `/api/dramas/${dramaId}/restructure`,
    { ...body, fingerprint, confirm: true },
    pcOnlyFetch(f),
  )
