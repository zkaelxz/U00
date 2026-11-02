// api/schemas/reader.py NovelFileStatus / NovelFileUploadResult / NovelReferenceRemoveResult, from the generated schema.
// Counts and booleans only: the API never returns a filename or path.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type NovelFileStatus = Schemas['NovelFileStatus']
export type NovelFileUploadResult = Schemas['NovelFileUploadResult']
export type NovelReferenceRemoveResult = Schemas['NovelReferenceRemoveResult']
