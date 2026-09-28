import type {
  JobStarted,
  MediaStatus,
  MediaUploadResult,
  NovelAttachResult,
  NovelMode,
  NovelStatus,
  TranscribeConfig,
  TranscribeConfigUpdate,
  TranscribeRunRequest,
  UploadAndTranscribeResult,
} from '../types/workspace'
import { getJson, postJson, postMultipart } from './client'

type Fetch = typeof fetch

export const getMediaStatus = (id: number, f?: Fetch) =>
  getJson<MediaStatus>(`/api/media/dramas/${id}/status`, f)

export const uploadMedia = (id: number, file: File, f?: Fetch) => {
  const form = new FormData()
  form.append('file', file)
  return postMultipart<MediaUploadResult>(`/api/media/dramas/${id}/upload`, form, f)
}

// Options travel as form fields (the endpoint takes Form(...), not JSON).
export const uploadAndTranscribe = (
  id: number,
  file: File,
  opts: TranscribeRunRequest,
  f?: Fetch,
) => {
  const form = new FormData()
  form.append('file', file)
  for (const [key, value] of Object.entries(opts)) {
    if (value !== undefined && value !== null) form.append(key, String(value))
  }
  return postMultipart<UploadAndTranscribeResult>(
    `/api/media/dramas/${id}/upload-and-transcribe`,
    form,
    f,
  )
}

export const getTranscribeConfig = (id: number, f?: Fetch) =>
  getJson<TranscribeConfig>(`/api/transcribe/dramas/${id}/config`, f)

export const updateTranscribeConfig = (id: number, update: TranscribeConfigUpdate, f?: Fetch) =>
  postJson<TranscribeConfig>(`/api/transcribe/dramas/${id}/config`, update, f)

export const startTranscribe = (id: number, req: TranscribeRunRequest, f?: Fetch) =>
  postJson<JobStarted>(`/api/transcribe/dramas/${id}/run`, req, f)

export const startDiarization = (id: number, expectedSpeakers?: number | null, f?: Fetch) => {
  const qs = expectedSpeakers == null ? '' : `?expected_speakers=${expectedSpeakers}`
  return postJson<JobStarted>(`/api/diarization/dramas/${id}/run${qs}`, undefined, f)
}

export const getNovelStatus = (id: number, f?: Fetch) =>
  getJson<NovelStatus>(`/api/novel/dramas/${id}/status`, f)

export const attachNovelText = (id: number, text: string, mode: NovelMode, f?: Fetch) =>
  postJson<NovelAttachResult>(`/api/novel/dramas/${id}/attach-text`, { text, mode }, f)

export const attachNovelEpub = (id: number, file: File, mode: NovelMode, f?: Fetch) => {
  const form = new FormData()
  form.append('file', file)
  form.append('mode', mode)
  return postMultipart<NovelAttachResult>(`/api/novel/dramas/${id}/attach-epub`, form, f)
}
