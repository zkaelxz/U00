import type { NovelChapterList, NovelChapterText } from '../types/novelChapters'
import { getJson } from './client'

type Fetch = typeof fetch

// api/routers/novel_files_routes.py: library.read, bounded (a list page is
// at most 200 rows, a text slice at most 50,000 characters).
export const getRawChapters = (dramaId: number, offset = 0, limit = 100, f?: Fetch) =>
  getJson<NovelChapterList>(`/api/novel/dramas/${dramaId}/raw-novel/chapters?offset=${offset}&limit=${limit}`, f)

export const getRawChapterText = (dramaId: number, number: number, offset = 0, limit = 20000, f?: Fetch) =>
  getJson<NovelChapterText>(
    `/api/novel/dramas/${dramaId}/raw-novel/chapters/${number}?offset=${offset}&limit=${limit}`,
    f,
  )
