// Mirrors api/schemas/transcribe.py Autotune* (transcribe_routes.py),
// characters.py NovelGlossary* (glossary_routes.py) and CharactersSeriesEntry
// (characters_routes.py), plus the PC-only stage deletes (delete_routes.py).
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export interface AutotuneRunRequest {
  candidates?: number[]
  initial_prompt?: string
  extra_names?: string
}

export type AutotuneRunResult = Schemas['AutotuneRunResult']

export type AutotuneCandidateScore = Schemas['AutotuneCandidateScore']

export type AutotuneStatus = Schemas['AutotuneStatus']

export type NovelGlossaryRunResult = Schemas['NovelGlossaryRunResult']

export interface NovelGlossaryProposal {
  term: string
  suggested_translation: string
  category: string | null
  policy: string | null
  reason: string
  already_in_glossary: boolean
  // Times the term occurs in the text the model read, and the other
  // renderings it proposed for it; confidence is derived from both.
  occurrences: number
  alternatives: string[]
  confidence: 'high' | 'low'
}

export type GlossaryDismissals = Schemas['GlossaryDismissals']

export type GlossaryDismissResult = Schemas['GlossaryDismissResult']

export interface NovelGlossaryStatus {
  job_id: string
  status: string
  progress: number | null
  message: string
  proposals: NovelGlossaryProposal[] | null
  // Names this run (null if unknown); an apply sends it back and gets a
  // 409 if the run was replaced since.
  run_id: string | null
}

export interface NovelGlossaryApplyRequest {
  terms: string[]
  overwrite_existing?: boolean
  confirm?: boolean
}

export type NovelGlossaryApplyResult = Schemas['NovelGlossaryApplyResult']

export type MediaRemoveResult = Schemas['MediaRemoveResult']

export type RawNovelRemoveResult = Schemas['RawNovelRemoveResult']

export type TranslationVersionDeleteResult = Schemas['TranslationVersionDeleteResult']

export type SeriesCharacterDeleteResult = Schemas['SeriesCharacterDeleteResult']

export type SeriesCharacter = Schemas['CharactersSeriesEntry']
