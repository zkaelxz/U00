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

export type NovelGlossaryProposal = Schemas['NovelGlossaryProposal']

export type GlossaryDismissals = Schemas['GlossaryDismissals']

export type GlossaryDismissResult = Schemas['GlossaryDismissResult']

export type NovelGlossaryStatus = Schemas['NovelGlossaryStatus']

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
