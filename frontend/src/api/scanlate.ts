// Automatic Scanlate routes (api/routers/scanlate_routes.py). Config and run
// notes need library.read; the run, render and export jobs need jobs.start
// (a paid engine also needs engines.paid); page upload is PC-only, so it goes
// through pcOnlyFetch (X-Baihe-Local: 1, and a 403 marks this tab remote).
// Jobs are polled and cancelled through /api/jobs.

import type {
  ScanlateConfig,
  ScanlateExportFormat,
  ScanlateJobStarted,
  ScanlateRunNotes,
  ScanlateRunRequest,
  ScanlateUploadResult,
} from '../types/scanlate'
import { artifactUrl, getJson, postJson, postMultipart } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const root = (id: number) => `/api/scanlate/dramas/${id}`

export const scanlateApi = {
  config: (id: number, f?: Fetch) => getJson<ScanlateConfig>(`${root(id)}/config`, f),
  runNotes: (id: number, f?: Fetch) => getJson<ScanlateRunNotes>(`${root(id)}/run-notes`, f),
  upload: (id: number, files: File[], sliceStrips: boolean, f?: Fetch) => {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    form.append('slice_strips', sliceStrips ? 'true' : 'false')
    return postMultipart<ScanlateUploadResult>(`${root(id)}/pages`, form, pcOnlyFetch(f))
  },
  run: (id: number, body: ScanlateRunRequest, f?: Fetch) =>
    postJson<ScanlateJobStarted>(`${root(id)}/run`, body, f),
  render: (id: number, pageId: number | null, f?: Fetch) =>
    postJson<ScanlateJobStarted>(`${root(id)}/render`, pageId ? { page_id: pageId } : {}, f),
  export: (id: number, formats: ScanlateExportFormat[], f?: Fetch) =>
    postJson<ScanlateJobStarted>(`${root(id)}/export`, { formats }, f),
}

// Download links for the export job's files (media.stream).
export const scanlateExportUrl = (id: number, format: ScanlateExportFormat) =>
  artifactUrl(id, format === 'zip' ? 'scanlate_zip' : 'scanlate_pdf')
