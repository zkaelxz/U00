import { readPref, writePref } from '../../hooks/usePersistedState'
import type { StorageLike } from '../../components/sectionStorage'
import type { TitleKind } from './titleKind'

/** The sub-tabs of the Media (Source) stage. */
export type MediaTab = 'add' | 'transcribe' | 'tools' | 'details' | 'credits'

const TABS: { id: MediaTab; label: string }[] = [
  { id: 'add', label: 'Add media' },
  { id: 'transcribe', label: 'Transcribe' },
  { id: 'tools', label: 'Tools' },
  { id: 'details', label: 'Details' },
  { id: 'credits', label: 'Credits' },
]

export const isMediaTab = (v: unknown): v is MediaTab => TABS.some((t) => t.id === v)

// Novels and comics have no audio to transcribe, so the two tabs about it would only be empty.
const NO_TRANSCRIBE: readonly TitleKind[] = ['novel', 'comic']

export function mediaTabsFor(kind: TitleKind): { id: MediaTab; label: string }[] {
  return NO_TRANSCRIBE.includes(kind) ? TABS.filter((t) => t.id !== 'transcribe' && t.id !== 'tools') : TABS
}

/** The tab to show: the wanted one when this kind has it, else Add media. */
export function resolveMediaTab(wanted: string | null | undefined, kind: TitleKind): MediaTab {
  return mediaTabsFor(kind).find((t) => t.id === wanted)?.id ?? 'add'
}

// Per title, so opening another title never lands on a tab chosen for this one.
const prefKey = (dramaId: number) => `source.tab.${dramaId}`

export function readMediaTab(storage: StorageLike | null, dramaId: number): MediaTab | null {
  const v = readPref(storage, prefKey(dramaId), '')
  return isMediaTab(v) ? v : null
}

export const writeMediaTab = (storage: StorageLike | null, dramaId: number, tab: MediaTab) =>
  writePref(storage, prefKey(dramaId), tab)


export const MEDIA_TAB_PREFIX = 'media'

/** The location hash with ?tab= set to `tab`, keeping any other query (like autofill) as it is. */
export function hashWithTab(hash: string, tab: MediaTab): string {
  const i = hash.indexOf('?')
  const path = i < 0 ? hash : hash.slice(0, i)
  const params = new URLSearchParams(i < 0 ? '' : hash.slice(i + 1))
  params.set('tab', tab)
  return `${path}?${params}`
}
