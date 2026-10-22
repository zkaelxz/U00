// Import a timed subtitle or lyric file into a title (api/routers/subtitle_import_routes.py).
// Preview and apply are uploads (media.upload).
import type {
  SubtitleImportOptions,
  SubtitleImportPreview,
  SubtitleImportResult,
} from '../types/subtitleImport'
import { postMultipart, type Fetch } from './client'

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
  postMultipart<SubtitleImportPreview>(`${base(id)}/preview`, form(file, o), f)

/** The server keeps nothing between preview and import, so the file is sent again. */
export const applySubtitle = (
  id: number,
  file: File,
  o: SubtitleImportOptions,
  confirm: { replaceLines: boolean; overwrite: boolean },
  f?: Fetch,
) => postMultipart<SubtitleImportResult>(`${base(id)}/apply`, form(file, o, confirm), f)
