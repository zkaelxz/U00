// Import a timed subtitle or lyric file into a title (api/routers/subtitle_import_routes.py).
// Preview and apply are PC-only uploads, so they go through pcOnlyFetch.
import type {
  SidecarMatchResult,
  SubtitleImportOptions,
  SubtitleImportPreview,
  SubtitleImportResult,
} from '../types/subtitleImport'
import { postJson, postMultipart } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const base = (id: number) => `/api/subtitle-import/dramas/${id}`

function form(file: File, o: SubtitleImportOptions, confirm?: { replaceLines: boolean; overwrite: boolean }) {
  const body = new FormData()
  body.append('file', file)
  body.append('mode', o.mode)
  if (o.encoding) body.append('encoding', o.encoding)
  if (o.splitBilingual) body.append('split_bilingual', 'true')
  if (o.splitBilingual && o.translationFirst) body.append('translation_first', 'true')
  if (confirm?.replaceLines) body.append('confirm_replace_lines', 'true')
  if (confirm?.overwrite) body.append('confirm_overwrite', 'true')
  return body
}

/** Parses and checks the file and says what importing it would do. Changes nothing. */
export const previewSubtitle = (id: number, file: File, o: SubtitleImportOptions, f?: Fetch) =>
  postMultipart<SubtitleImportPreview>(`${base(id)}/preview`, form(file, o), pcOnlyFetch(f))

/** The server keeps nothing between preview and import, so the file is sent again. */
export const applySubtitle = (
  id: number,
  file: File,
  o: SubtitleImportOptions,
  confirm: { replaceLines: boolean; overwrite: boolean },
  f?: Fetch,
) => postMultipart<SubtitleImportResult>(`${base(id)}/apply`, form(file, o, confirm), pcOnlyFetch(f))

/** Ranks the picked file names against a media file name (names only). */
export const rankSidecars = (id: number, mediaName: string, names: string[], f?: Fetch) =>
  postJson<SidecarMatchResult>(`${base(id)}/sidecars`, { media_name: mediaName, names }, f)
