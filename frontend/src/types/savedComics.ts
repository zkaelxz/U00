// Saved manga (/api/saved-comics), from the generated schema: chapters saved as CBZ files, read in the
// app. Series and chapters are named, never given as paths; only the PC-only
// folder routes return a path (the PC's own save folder).
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type SavedComicsFolder = Schemas['SavedComicsFolder']
export type SavedSeries = Schemas['SavedSeries']
export type SavedChapterList = Schemas['SavedChapterList']
export type SavedChapterPages = Schemas['SavedChapterPages']
export type SavedPage = Schemas['SavedPage']
