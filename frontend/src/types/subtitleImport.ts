// Mirrors api/schemas/subtitle_import.py.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type SubtitleImportMode = 'source' | 'translation'

export type SubtitleImportPreview = Schemas['SubtitleImportPreview']

export type SubtitleImportResult = Schemas['SubtitleImportResult']

export interface SubtitleImportOptions {
  mode: SubtitleImportMode
  encoding: string
  splitBilingual: boolean
  translationFirst: boolean
}
