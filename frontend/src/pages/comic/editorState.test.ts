import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { ScanlatePageDetail, ScanlateRegion } from '../../types/scanlate'
import {
  applyEdit,
  changedFields,
  draftFields,
  draftOf,
  draftValid,
  parseSize,
  undoEdit,
  type EditorApi,
  type EditorState,
} from './editorState'

const region = (over: Partial<ScanlateRegion> = {}): ScanlateRegion => ({
  id: 5, idx: 0, x: 10, y: 20, w: 100, h: 50, source_text: '原文', translated_text: 'Hello',
  font_size: 18, font_category: 'regular', text_color: null, ...over,
})
const detail = (over: Partial<ScanlatePageDetail> = {}): ScanlatePageDetail => ({
  id: 1, drama_id: 7, ordinal: 1, width: 800, height: 1200, rev: 3, undo_depth: 0, has_rendered: false,
  regions: [region()], ...over,
})
const conflict = (reason: string) => new ApiError(409, { code: 'conflict', message: 'Changed.', details: { reason, rev: 9 } }, 'PATCH')

function fakeApi(over: Partial<EditorApi> = {}): EditorApi {
  return {
    page: vi.fn(async () => detail({ rev: 9 })),
    editRegion: vi.fn(async () => detail({ rev: 4, undo_depth: 1 })),
    undo: vi.fn(async () => detail({ rev: 5 })),
    ...over,
  }
}
const start: EditorState = { page: detail(), error: null }

describe('applyEdit', () => {
  it('sends one PATCH with the rev and the old values of what changed', async () => {
    const api = fakeApi()
    const next = await applyEdit(start, api, 5, { translated_text: 'Hi', x: 10, font_size: 20 })
    expect(api.editRegion).toHaveBeenCalledTimes(1)
    expect(api.editRegion).toHaveBeenCalledWith(5, {
      expected_rev: 3,
      expected: { translated_text: 'Hello', font_size: 18 },
      changes: { translated_text: 'Hi', font_size: 20 },
    })
    expect(next.page?.rev).toBe(4)
    expect(next.error).toBeNull()
  })

  it('sends nothing when no field differs', async () => {
    const api = fakeApi()
    const next = await applyEdit(start, api, 5, { translated_text: 'Hello', x: 10 })
    expect(api.editRegion).not.toHaveBeenCalled()
    expect(next.page).toBe(start.page)
  })

  it('uses the rev of the latest response for the next write', async () => {
    const api = fakeApi()
    const once = await applyEdit(start, api, 5, { x: 11 })
    await applyEdit(once, api, 5, { x: 12 })
    expect(vi.mocked(api.editRegion).mock.calls.map((c) => c[1].expected_rev)).toEqual([3, 4])
  })

  it.each(['stale', 'history_changed'])('on 409 %s reloads the page and keeps the error', async (reason) => {
    const err = conflict(reason)
    const api = fakeApi({ editRegion: vi.fn(async () => { throw err }) })
    const next = await applyEdit(start, api, 5, { x: 11 })
    expect(api.page).toHaveBeenCalledTimes(1)
    expect(next.page?.rev).toBe(9)
    expect(next.error).toBe(err)
  })

  it('keeps the old page and the error when the reload fails too', async () => {
    const err = conflict('stale')
    const api = fakeApi({ editRegion: vi.fn(async () => { throw err }), page: vi.fn(async () => { throw new Error('down') }) })
    const next = await applyEdit(start, api, 5, { x: 11 })
    expect(next.page).toBe(start.page)
    expect(next.error).toBe(err)
  })

  it('does not reload on another failure', async () => {
    const err = new ApiError(500, { code: 'internal', message: 'No.' }, 'PATCH')
    const api = fakeApi({ editRegion: vi.fn(async () => { throw err }) })
    const next = await applyEdit(start, api, 5, { x: 11 })
    expect(api.page).not.toHaveBeenCalled()
    expect(next).toEqual({ page: start.page, error: err })
  })

  it('ignores a box that is not on the page', async () => {
    const api = fakeApi()
    expect(await applyEdit(start, api, 99, { x: 1 })).toBe(start)
    expect(api.editRegion).not.toHaveBeenCalled()
  })
})

describe('undoEdit', () => {
  it('sends the current rev and takes the new one', async () => {
    const api = fakeApi()
    const next = await undoEdit({ page: detail({ rev: 4, undo_depth: 1 }), error: null }, api)
    expect(api.undo).toHaveBeenCalledWith(4)
    expect(next.page?.rev).toBe(5)
  })

  it.each(['history_changed', 'nothing_to_undo'])('on 409 %s reloads and shows the error', async (reason) => {
    const err = conflict(reason)
    const api = fakeApi({ undo: vi.fn(async () => { throw err }) })
    const next = await undoEdit(start, api)
    expect(api.page).toHaveBeenCalledTimes(1)
    expect(next.page?.rev).toBe(9)
    expect(next.error).toBe(err)
  })
})

describe('draft', () => {
  it('turns the form back into only the changed fields', () => {
    const r = region()
    const d = { ...draftOf(r), text: 'Hi', color: '#b3261e' }
    expect(changedFields(r, draftFields(d))).toEqual({ translated_text: 'Hi', text_color: '#b3261e' })
  })
  it('checks size and length against the server limits', () => {
    expect(parseSize('6')).toBe(6)
    expect(parseSize('200')).toBe(200)
    for (const bad of ['5', '201', '', '1.5', 'abc', '-7']) expect(parseSize(bad)).toBeNull()
    const ok = draftOf(region())
    expect(draftValid(ok)).toBe(true)
    expect(draftValid({ ...ok, text: 'a'.repeat(2001) })).toBe(false)
    expect(draftValid({ ...ok, text: 'a'.repeat(2000) })).toBe(true)
    expect(draftValid({ ...ok, size: '5' })).toBe(false)
  })
})
