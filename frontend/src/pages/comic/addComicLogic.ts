// Pure steps of the "Add a Comic" dialog (AddComicDialog.tsx): what is needed before Add is enabled, the
// pages inside a CBZ, and the title-then-content sequence with its dependencies passed in so it is unit-tested.

import { unzipSync } from 'fflate'

import type { DramaCreateRequest, DramaCreateResult } from '../../types/library'
import type { ScanlateUploadResult } from '../../types/scanlate'
import type { SourcesJobStarted } from '../../types/sources'
import { defaultPrefs, type ComicMode, type ComicPrefs } from './comicLogic'

export type AddWay = 'link' | 'images' | 'file'

export const ADD_WAYS: { id: AddWay; label: string }[] = [
  { id: 'link', label: 'Link' },
  { id: 'images', label: 'Images' },
  { id: 'file', label: 'CBZ' },
]

export interface AddComicInput {
  title: string
  way: AddWay
  link: string
  files: File[]
}

const IMAGE = /\.(png|jpe?g|webp)$/i

export function canAdd(i: AddComicInput): boolean {
  if (!i.title.trim()) return false
  if (i.way === 'link') return /^https?:\/\/\S+$/i.test(i.link.trim())
  return i.files.length > 0
}

// "page10" sorts after "page9", as a reader numbers pages.
const byName = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })

/** The page images of a CBZ in reading order, as files the page upload takes. Folders and macOS metadata are skipped. */
export function cbzPages(bytes: Uint8Array): File[] {
  const entries = unzipSync(bytes, {
    filter: (f) => IMAGE.test(f.name) && !f.name.startsWith('__MACOSX/') && !f.name.split('/').pop()!.startsWith('.'),
  })
  return Object.keys(entries)
    .sort(byName)
    .map((path) => new File([entries[path] as Uint8Array<ArrayBuffer>], path.split('/').pop()!))
}

/** The viewer prefs a new title starts with: the media type's defaults, reading in the chosen mode. */
export function startPrefs(mediaType: string, mode: ComicMode): ComicPrefs {
  const base = defaultPrefs(mediaType)
  return { ...base, mode, fit: mode === 'paged' ? 'height' : 'width' }
}

export interface AddComicDeps {
  create: (body: DramaCreateRequest) => Promise<DramaCreateResult>
  importLink: (url: string, dramaId: number) => Promise<SourcesJobStarted>
  upload: (dramaId: number, files: File[]) => Promise<ScanlateUploadResult>
}

/**
 * Makes the title, then adds the pages by the chosen way. `existing` is a title an earlier attempt made, so
 * pressing Add again after a failed import retries into it instead of making a second empty title.
 */
export async function addComic(
  i: AddComicInput,
  request: DramaCreateRequest,
  existing: number | null,
  d: AddComicDeps,
): Promise<number> {
  const id = existing ?? (await d.create(request)).id
  if (i.way === 'link') await d.importLink(i.link.trim(), id)
  else await d.upload(id, i.files)
  return id
}
