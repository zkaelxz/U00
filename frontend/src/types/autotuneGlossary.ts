// Mirrors api/schemas.py: Autotune* (route batch 2C, transcribe_routes.py),
// NovelGlossary* (glossary_routes.py) and the PC-only stage deletes
// (delete_routes.py) plus CharactersSeriesEntry (characters_routes.py).

export interface AutotuneRunRequest {
  candidates?: number[]
  initial_prompt?: string
}

export interface AutotuneRunResult {
  job_id: string
  candidates: number[]
}

export interface AutotuneCandidateScore {
  candidate_ms: number
  long_lines: number
  total_lines: number
}

export interface AutotuneStatus {
  job_id: string
  status: string
  progress: number | null
  message: string
  results: AutotuneCandidateScore[] | null
  best_candidate_ms: number | null
}

export interface NovelGlossaryRunResult {
  job_id: string
  engine: string
  paired: boolean
}

export interface NovelGlossaryProposal {
  term: string
  suggested_translation: string
  category: string | null
  policy: string | null
  reason: string
  already_in_glossary: boolean
}

export interface NovelGlossaryStatus {
  job_id: string
  status: string
  progress: number | null
  message: string
  proposals: NovelGlossaryProposal[] | null
}

export interface NovelGlossaryApplyRequest {
  terms: string[]
  overwrite_existing?: boolean
  confirm?: boolean
}

export interface NovelGlossaryApplyResult {
  added: string[]
  overwritten: string[]
  skipped_existing: string[]
  unknown: string[]
}

export interface MediaRemoveResult {
  drama_id: number
  removed: boolean
  audio_file_removed: boolean
  video_file_removed: boolean
  has_audio: boolean
  has_video: boolean
}

export interface RawNovelRemoveResult {
  drama_id: number
  removed: boolean
  has_raw_novel_context: boolean
}

export interface TranslationVersionDeleteResult {
  drama_id: number
  version_id: number
  deleted: boolean
  was_active: boolean
}

export interface SeriesCharacterDeleteResult {
  series_id: number
  character_id: number
  deleted: boolean
}

export interface SeriesCharacter {
  id: number
  character_name: string
  aliases: string
  notes: string
  pronouns: string
}
