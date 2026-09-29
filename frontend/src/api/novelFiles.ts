import type { NovelFileStatus, NovelFileUploadResult, NovelReferenceRemoveResult } from '../types/novelFiles'
import { getJson, postJson, postMultipart } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

// api/routers/novel_files_routes.py. Status reads are library.read; the
// uploads and the reference removal are PC-only (pcOnlyFetch adds
// X-Baihe-Local: 1 and marks the tab remote on a 403). Every write is a
// 409 while a job for the drama runs. Raw-novel removal is
// stageDeletes.removeRawNovel.

function upload(path: string, file: File, f?: Fetch) {
  const form = new FormData()
  form.append('file', file)
  return postMultipart<NovelFileUploadResult>(path, form, pcOnlyFetch(f))
}

export const getNovelReference = (dramaId: number, f?: Fetch) =>
  getJson<NovelFileStatus>(`/api/novel/dramas/${dramaId}/reference`, f)

export const uploadNovelReference = (dramaId: number, file: File, f?: Fetch) =>
  upload(`/api/novel/dramas/${dramaId}/reference`, file, f)

export const removeNovelReference = (dramaId: number, f?: Fetch) =>
  postJson<NovelReferenceRemoveResult>(
    `/api/novel/dramas/${dramaId}/reference/remove`,
    { confirm: true },
    pcOnlyFetch(f),
  )

export const getRawNovel = (dramaId: number, f?: Fetch) =>
  getJson<NovelFileStatus>(`/api/novel/dramas/${dramaId}/raw-novel`, f)

export const uploadRawNovel = (dramaId: number, file: File, f?: Fetch) =>
  upload(`/api/novel/dramas/${dramaId}/raw-novel`, file, f)

// The paste box: JSON {text}, same caps, 409 and PC-only rule as the upload.
export const saveNovelReferenceText = (dramaId: number, text: string, f?: Fetch) =>
  postJson<NovelFileUploadResult>(`/api/novel/dramas/${dramaId}/reference/text`, { text }, pcOnlyFetch(f))

export const saveRawNovelText = (dramaId: number, text: string, f?: Fetch) =>
  postJson<NovelFileUploadResult>(`/api/novel/dramas/${dramaId}/raw-novel/text`, { text }, pcOnlyFetch(f))
