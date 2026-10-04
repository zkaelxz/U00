// Comic viewer API (routes under /api/scanlate/dramas/{id}). The page list
// and progress need library.read, page text lines.read, saving progress
// lines.edit, and the page images media.stream (so an <img> can fail with 403).

import type {
  ComicPagesResponse,
  ComicProgress,
  ComicRegionsResponse,
  ComicVariant,
} from '../types/comic'
import { apiUrl, getJson, headStatus, postJson } from './client'

type Fetch = typeof fetch

const root = (id: number) => `/api/scanlate/dramas/${id}`

// The <img> src for one page. `version` (C1's image_version) busts the cache
// when a typeset page is rewritten in place.
export function comicImageUrl(
  dramaId: number,
  pageId: number,
  variant: ComicVariant = 'original',
  version: number | string | null = null,
): string {
  const q = new URLSearchParams({ variant })
  if (version !== null && version !== '') q.set('v', String(version))
  return apiUrl(`${root(dramaId)}/pages/${pageId}/image?${q}`)
}

export type ImageProblem = 'forbidden' | 'missing' | 'failed'

// After an <img> fails, ask the server why (HEAD, no body), so the viewer can
// say "needs media playback permission" instead of showing a broken image.
export async function probeImage(url: string, fetchImpl: Fetch = fetch): Promise<ImageProblem> {
  try {
    const status = await headStatus(url, fetchImpl)
    if (status === 401 || status === 403) return 'forbidden'
    if (status === 404) return 'missing'
    return 'failed'
  } catch {
    return 'failed'
  }
}

export const comicApi = {
  pages: (id: number, f?: Fetch) => getJson<ComicPagesResponse>(`${root(id)}/pages`, f),
  regions: (id: number, pageId: number, f?: Fetch) =>
    getJson<ComicRegionsResponse>(`${root(id)}/pages/${pageId}/regions`, f),
  progress: (id: number, f?: Fetch) => getJson<ComicProgress>(`${root(id)}/progress`, f),
  // Only the page is sent; the server works out percent_complete.
  saveProgress: (id: number, page: number, f?: Fetch) =>
    postJson<ComicProgress>(`${root(id)}/progress`, { page }, f),
}
