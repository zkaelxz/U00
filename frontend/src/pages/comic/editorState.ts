// The comic editor's write flow, free of React so it is unit-tested: every
// write carries the page rev from the last response, and a refused write
// reloads the page so the next one starts from what the server holds.

import { hasStatus } from '../../api/client'
import {
  MAX_FONT_SIZE,
  MAX_REGION_TEXT_CHARS,
  MIN_FONT_SIZE,
  type ScanlatePageDetail,
  type ScanlateRegion,
  type ScanlateRegionEdit,
  type ScanlateRegionFields,
} from '../../types/scanlate'
import type { Box } from './editorGeometry'

export interface EditorApi {
  page: () => Promise<ScanlatePageDetail>
  editRegion: (regionId: number, edit: ScanlateRegionEdit) => Promise<ScanlatePageDetail>
  createRegion: (box: Box, expectedRev: number) => Promise<ScanlatePageDetail>
  deleteRegion: (regionId: number, expectedRev: number) => Promise<ScanlatePageDetail>
  undo: (expectedRev: number) => Promise<ScanlatePageDetail>
}

export interface EditorState {
  page: ScanlatePageDetail | null
  error: unknown
}

const FIELDS = ['translated_text', 'x', 'y', 'w', 'h', 'font_size', 'font_category', 'text_color'] as const

/** Only the fields that differ from the box as the editor last saw it. */
export function changedFields(region: ScanlateRegion, next: ScanlateRegionFields): ScanlateRegionFields {
  const out: Record<string, unknown> = {}
  for (const k of FIELDS) if (k in next && next[k] !== region[k]) out[k] = next[k]
  return out as ScanlateRegionFields
}

async function reloaded(api: EditorApi, error: unknown, fallback: ScanlatePageDetail | null): Promise<EditorState> {
  // A refusal means the page moved on; showing the old boxes would invite the same refusal again.
  try {
    return { page: await api.page(), error }
  } catch {
    return { page: fallback, error }
  }
}

/** One PATCH for the box, or none when nothing differs. */
export async function applyEdit(
  state: EditorState,
  api: EditorApi,
  regionId: number,
  next: ScanlateRegionFields,
): Promise<EditorState> {
  const page = state.page
  const region = page?.regions.find((r) => r.id === regionId)
  if (!page || !region) return state
  const changes = changedFields(region, next)
  const keys = Object.keys(changes) as (keyof ScanlateRegionFields)[]
  if (keys.length === 0) return { ...state, error: null }
  const expected: Record<string, unknown> = {}
  for (const k of keys) expected[k] = region[k]
  try {
    const updated = await api.editRegion(regionId, { expected_rev: page.rev, expected: expected as ScanlateRegionFields, changes })
    return { page: updated, error: null }
  } catch (e) {
    return hasStatus(e, 409) ? reloaded(api, e, page) : { page, error: e }
  }
}

/** One POST for a drawn box; the server appends it, so it is the last region of the answer. */
export async function createBox(state: EditorState, api: EditorApi, box: Box): Promise<EditorState> {
  const page = state.page
  if (!page) return state
  try {
    return { page: await api.createRegion(box, page.rev), error: null }
  } catch (e) {
    return hasStatus(e, 409) ? reloaded(api, e, page) : { page, error: e }
  }
}

/** One DELETE for the box, carrying the rev of the last response. */
export async function deleteBox(state: EditorState, api: EditorApi, regionId: number): Promise<EditorState> {
  const page = state.page
  if (!page || !page.regions.some((r) => r.id === regionId)) return state
  try {
    return { page: await api.deleteRegion(regionId, page.rev), error: null }
  } catch (e) {
    return hasStatus(e, 409) ? reloaded(api, e, page) : { page, error: e }
  }
}

export async function undoEdit(state: EditorState, api: EditorApi): Promise<EditorState> {
  const page = state.page
  if (!page) return state
  try {
    return { page: await api.undo(page.rev), error: null }
  } catch (e) {
    return hasStatus(e, 409) ? reloaded(api, e, page) : { page, error: e }
  }
}

export interface Draft {
  text: string
  fontCategory: string
  size: string
  color: string | null
}

export function draftOf(r: ScanlateRegion): Draft {
  return { text: r.translated_text, fontCategory: r.font_category, size: String(r.font_size), color: r.text_color }
}

/** The size as a whole number the server accepts, or null. */
export function parseSize(size: string): number | null {
  if (!/^\d+$/.test(size.trim())) return null
  const n = Number(size)
  return n >= MIN_FONT_SIZE && n <= MAX_FONT_SIZE ? n : null
}

export function draftValid(d: Draft): boolean {
  return d.text.length <= MAX_REGION_TEXT_CHARS && parseSize(d.size) !== null
}

/** What Apply sends for a valid draft (changedFields drops what did not change). */
export function draftFields(d: Draft): ScanlateRegionFields {
  return { translated_text: d.text, font_category: d.fontCategory, font_size: parseSize(d.size) ?? undefined, text_color: d.color }
}
