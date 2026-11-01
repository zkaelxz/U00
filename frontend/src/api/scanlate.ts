// Automatic Scanlate routes (api/routers/scanlate_routes.py). Config and run
// notes need library.read; the run, render and export jobs need jobs.start
// (a paid engine also needs engines.paid); page upload is media.upload (an
// admin; from away only while the PC's remote-uploads switch is on).
// Jobs are polled and cancelled through /api/jobs.

import type {
  ScanlateConfig,
  ScanlatePageDetail,
  ScanlateRegionCreate,
  ScanlateRegionEdit,
  ScanlateExportFormat,
  ScanlateJobStarted,
  ScanlateRunNotes,
  ScanlateRunRequest,
  ScanlateUploadResult,
} from '../types/scanlate'
import { artifactUrl, deleteJson, getJson, patchJson, postJson, postMultipart, type Fetch } from './client'

const root = (id: number) => `/api/scanlate/dramas/${id}`

export const scanlateApi = {
  config: (id: number, f?: Fetch) => getJson<ScanlateConfig>(`${root(id)}/config`, f),
  runNotes: (id: number, f?: Fetch) => getJson<ScanlateRunNotes>(`${root(id)}/run-notes`, f),
  upload: (id: number, files: File[], sliceStrips: boolean, f?: Fetch) => {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    form.append('slice_strips', sliceStrips ? 'true' : 'false')
    return postMultipart<ScanlateUploadResult>(`${root(id)}/pages`, form, f)
  },
  // The page's text boxes with its edit revision (lines.read); the editor sends this rev back with every write.
  page: (id: number, pageId: number, f?: Fetch) => getJson<ScanlatePageDetail>(`${root(id)}/pages/${pageId}`, f),
  // One text box, compare-and-set on expectedRev (lines.edit). `expected` checks the box's own values too.
  editRegion: (id: number, pageId: number, regionId: number, edit: ScanlateRegionEdit, f?: Fetch) =>
    patchJson<ScanlatePageDetail>(`${root(id)}/pages/${pageId}/regions/${regionId}`, edit, f),
  createRegion: (id: number, pageId: number, body: ScanlateRegionCreate, f?: Fetch) =>
    postJson<ScanlatePageDetail>(`${root(id)}/pages/${pageId}/regions`, body, f),
  // The rev is a query parameter because some proxies drop a DELETE body.
  deleteRegion: (id: number, pageId: number, regionId: number, expectedRev: number, f?: Fetch) =>
    deleteJson<ScanlatePageDetail>(`${root(id)}/pages/${pageId}/regions/${regionId}?expected_rev=${expectedRev}`, f),
  undo: (id: number, pageId: number, expectedRev: number, f?: Fetch) =>
    postJson<ScanlatePageDetail>(`${root(id)}/pages/${pageId}/undo`, { expected_rev: expectedRev }, f),
  run: (id: number, body: ScanlateRunRequest, f?: Fetch) =>
    postJson<ScanlateJobStarted>(`${root(id)}/run`, body, f),
  render: (id: number, pageId: number | null, f?: Fetch) =>
    postJson<ScanlateJobStarted>(`${root(id)}/render`, pageId ? { page_id: pageId } : {}, f),
  // typeset=false exports the original pages; the server's default is the typeset ones.
  export: (id: number, formats: ScanlateExportFormat[], f?: Fetch, typeset = true) =>
    postJson<ScanlateJobStarted>(`${root(id)}/export`, typeset ? { formats } : { formats, typeset }, f),
}

// Download links for the export job's files (media.stream).
export const scanlateExportUrl = (id: number, format: ScanlateExportFormat) =>
  artifactUrl(id, format === 'zip' ? 'scanlate_zip' : 'scanlate_pdf')
