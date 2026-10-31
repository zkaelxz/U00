import type { Page } from '@playwright/test'

import { mockComic, pageIdOf, type ComicMockState } from './comicMocks'
import { json } from './mockHelpers'

// Stubbed backend for the comic editor specs, on top of mockComic (page list and
// images): the page detail with its rev, PATCH of one box, undo, render and the
// render job. It keeps the same compare-and-set rule as the real routes, so a
// spec that sends a wrong rev sees the 409.

export interface EditorRegion {
  id: number
  idx: number
  x: number
  y: number
  w: number
  h: number
  source_text: string
  translated_text: string
  font_size: number
  font_category: string
  kind: string
  skip: boolean
  include_sfx: boolean
  text_color: string | null
}

export interface EditorStub {
  comic: ComicMockState
  rev: number
  regions: EditorRegion[]
  history: EditorRegion[][]
  // The next write is refused as if another writer had moved the page on.
  conflictNext: boolean
  patches: { path: string; body: { expected_rev: number; expected?: unknown; changes: Record<string, unknown> } }[]
  undos: { expected_rev: number }[]
  renders: unknown[]
  pageGets: number
}

const region = (id: number, idx: number, x: number, y: number, text: string, source: string): EditorRegion => ({
  id, idx, x, y, w: 300, h: 100, source_text: source, translated_text: text, font_size: 18, font_category: 'regular',
  kind: 'bubble', skip: false, include_sfx: false, text_color: null,
})

export async function mockEditor(page: Page): Promise<EditorStub> {
  const comic = await mockComic(page, { id: 7, pageCount: 4, width: 800, height: 1200, rendered: [0] })
  const stub: EditorStub = {
    comic, rev: 1, history: [], conflictNext: false, patches: [], undos: [], renders: [], pageGets: 0,
    regions: [
      region(11, 0, 100, 100, 'I told you not to open that door.', '我告诉过你别开那扇门。'),
      region(12, 1, 400, 600, 'Then who is standing behind you?', '那你身后站着的是谁？'),
    ],
  }
  const detail = () => ({
    id: pageIdOf(7, 0), drama_id: 7, ordinal: 1, width: 800, height: 1200, rev: stub.rev, undo_depth: stub.history.length,
    has_rendered: true, regions: stub.regions, run_notes: [],
  })
  const refuse = (route: import('@playwright/test').Route, reason: string) =>
    json(route, { error: { code: 'conflict', message: 'The page changed. Reload it and try again.', details: { reason, rev: stub.rev } } }, 409)

  const pid = pageIdOf(7, 0)
  await page.route(new RegExp(`/api/scanlate/dramas/7/pages/${pid}$`), (route) => {
    stub.pageGets += 1
    return json(route, detail())
  })
  await page.route(new RegExp(`/api/scanlate/dramas/7/pages/${pid}/regions/\\d+$`), (route) => {
    const req = route.request()
    const body = req.postDataJSON()
    stub.patches.push({ path: new URL(req.url()).pathname, body })
    if (stub.conflictNext) {
      stub.conflictNext = false
      stub.rev += 1
      return refuse(route, 'stale')
    }
    if (body.expected_rev !== stub.rev) return refuse(route, 'stale')
    const rid = Number(new URL(req.url()).pathname.split('/').at(-1))
    stub.history.push(structuredClone(stub.regions))
    stub.regions = stub.regions.map((r) => (r.id === rid ? { ...r, ...body.changes } : r))
    stub.rev += 1
    return json(route, detail())
  })
  await page.route(new RegExp(`/api/scanlate/dramas/7/pages/${pid}/undo$`), (route) => {
    const body = route.request().postDataJSON()
    stub.undos.push(body)
    if (body.expected_rev !== stub.rev) return refuse(route, 'stale')
    const prev = stub.history.pop()
    if (!prev) return refuse(route, 'nothing_to_undo')
    stub.regions = prev
    stub.rev += 1
    return json(route, detail())
  })
  await page.route(/\/api\/scanlate\/dramas\/7\/render$/, (route) => {
    stub.renders.push(route.request().postDataJSON())
    return json(route, { job_id: 'scanlate_7' })
  })
  await page.route(/\/api\/jobs\/scanlate_7$/, (route) =>
    json(route, {
      job_id: 'scanlate_7', status: 'done', progress: 1, message: '', error: null, description: null, gpu_touching: false,
      started_at: 1, finished_at: 2, updated_at: 2,
    }))
  return stub
}
