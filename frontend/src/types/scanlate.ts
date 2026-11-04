// Automatic Scanlate API shapes (api/scanlate_schemas.py); responses come from the generated schema.
// No path, file name or key ever comes back from these routes.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type ScanlateEngine = Schemas['ScanlateEngine']
export type ScanlateUploadLimits = Schemas['ScanlateUploadLimits']

export type ScanlateDetectBackend = 'auto' | 'cv' | 'ml'

export type ScanlateConfig = Schemas['ScanlateConfig']
export type ScanlatePageNotes = Schemas['ScanlatePageNotes']
export type ScanlateRunNotes = Schemas['ScanlateRunNotes']
export type ScanlateUploadResult = Schemas['ScanlateUploadResult']

export type ScanlateRunMode = 'missing' | 'page' | 'all'

export type ScanlateRunRequest = Schemas['ScanlateRunRequest']

export type ScanlateExportFormat = 'zip' | 'pdf'

export type ScanlateJobStarted = Schemas['ScanlateJobStarted']
export type ScanlateRegion = Schemas['ScanlateRegion']

export type ScanlatePageDetail = Schemas['ScanlatePageDetail']

// The fields a box edit may change; only the ones present count.
export type ScanlateRegionFields = Partial<
  Pick<ScanlateRegion, 'translated_text' | 'x' | 'y' | 'w' | 'h' | 'font_size' | 'font_category' | 'kind' | 'text_color'>
>

export interface ScanlateRegionEdit {
  expected_rev: number
  expected?: ScanlateRegionFields
  changes: ScanlateRegionFields
}

// A drawn box: only its rectangle; the server fills the rest (empty English, automatic colour).
export interface ScanlateRegionCreate {
  expected_rev: number
  region: { x: number; y: number; w: number; h: number }
}

export const MAX_REGION_TEXT_CHARS = 2000
export const MIN_FONT_SIZE = 6
export const MAX_FONT_SIZE = 200
