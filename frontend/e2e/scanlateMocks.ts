import type { Page, Route } from '@playwright/test'

import { mockComic, type ComicMockOptions, type ComicMockState } from './comicMocks'

// The Translate panel's routes (api/routers/scanlate_routes.py) on top of the
// comic viewer mocks. A run or export job goes running -> done over two polls;
// once a run is done the page list reports every page typeset.

export interface ScanlateMockState extends ComicMockState {
  runs: unknown[]
  exports: unknown[]
  uploads: number
  jobPolls: number
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

export async function mockScanlate(page: Page, over: Partial<ComicMockOptions> = {}): Promise<ScanlateMockState> {
  const base = await mockComic(page, over)
  const s = base as ScanlateMockState
  s.runs = []
  s.exports = []
  s.uploads = 0
  s.jobPolls = 0
  const { id } = s.opts
  const root = `/api/scanlate/dramas/${id}`
  const jobId = `scanlate_${id}`
  let jobDone = true

  await page.route(new RegExp(`${root}/config$`), (route) =>
    json(route, {
      drama_id: id, source_language: 'zh',
      engines: [
        { name: 'claude', label: 'Claude (paid)', free: false, key_configured: false },
        { name: 'ollama', label: 'Ollama (free, local)', free: true, key_configured: true },
      ],
      default_engine: 'claude', detect_backends: ['auto', 'cv', 'ml'],
      ml_weights_cached: false, lama_weights_cached: false, ocr_backend: 'paddle', ocr_backend_installed: true,
      page_count: s.opts.pageCount, pages_with_regions: 0, pages_rendered: s.opts.rendered.length,
      job_id: jobId, job_running: false,
      upload_limits: {
        image_types: ['png', 'jpg', 'jpeg', 'webp'], pdf: true, max_image_mb: 30, max_image_megapixels: 100,
        max_pdf_mb: 300, max_pdf_pages: 500, max_files: 300, max_total_mb: 1024, strip_slice_ratio: 3,
        slice_strips_default: true,
      },
    }),
  )
  await page.route(new RegExp(`${root}/run-notes$`), (route) =>
    json(route, {
      drama_id: id,
      pages: jobDone && s.runs.length
        ? [{ page_id: id * 100 + 1, ordinal: 1, notes: [{ level: 'warning', message: '<b>1 region</b> had no translation.' }] }]
        : [],
    }),
  )
  await page.route(new RegExp(`${root}/pages$`), async (route) => {
    if (route.request().method() === 'POST') {
      s.uploads += 1
      return json(route, { added: 2, page_ids: [1, 2], pdf_pages_skipped: 0, strips_sliced: 1 })
    }
    return route.fallback()
  })
  await page.route(new RegExp(`${root}/run$`), (route) => {
    s.runs.push(route.request().postDataJSON())
    jobDone = false
    return json(route, { job_id: jobId, engine: 'ollama', mode: 'missing' })
  })
  await page.route(new RegExp(`${root}/export$`), (route) => {
    s.exports.push(route.request().postDataJSON())
    jobDone = false
    return json(route, { job_id: jobId })
  })
  await page.route(new RegExp(`/api/jobs/${jobId}$`), (route) => {
    s.jobPolls += 1
    const finishing = !jobDone && s.jobPolls >= 2
    if (finishing) {
      jobDone = true
      if (s.runs.length) s.opts.rendered = Array.from({ length: s.opts.pageCount }, (_, i) => i)
    }
    return json(route, {
      job_id: jobId, status: jobDone ? 'done' : 'running', progress: jobDone ? 1 : 0.5,
      message: jobDone ? 'Pages: 3 translated.' : 'Page 2 of 3', error: null, description: null,
      gpu_touching: true, started_at: 1, finished_at: jobDone ? 2 : null, updated_at: 2, result: null,
      outcome: jobDone ? 'ok' : null,
    })
  })
  return s
}
