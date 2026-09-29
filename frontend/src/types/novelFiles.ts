// api/schemas.py NovelFileStatus / NovelFileUploadResult / NovelReferenceRemoveResult.
// Counts and booleans only: the API never returns a filename or path.
export interface NovelFileStatus {
  drama_id: number
  present: boolean
  size_bytes: number
  char_count: number
}

export interface NovelFileUploadResult extends NovelFileStatus {
  replaced: boolean
}

export interface NovelReferenceRemoveResult {
  drama_id: number
  removed: boolean
  present: boolean
}
