import type { components } from './generated/schema'
import { getJson, postJson, type Fetch } from './client'

export type CastProposal = components['schemas']['CastProposal']
export type CastPerson = components['schemas']['CastPerson']
export type CastApplyResult = components['schemas']['CastApplyResult']
export type Pronouns = 'she/her' | 'he/him' | 'they/them'

// api/routers/characters_routes.py: library.read + lines.read. The people the glossary scan found; makes no model call.
export const getCastProposal = (dramaId: number, f?: Fetch) =>
  getJson<CastProposal>(`/api/characters/dramas/${dramaId}/cast-proposal`, f)
// lines.edit. Sets the chosen pronouns where none are set; never replaces existing ones.
export const applyCastProposal = (dramaId: number, people: { name: string; pronouns: Pronouns }[], f?: Fetch) =>
  postJson<CastApplyResult>(`/api/characters/dramas/${dramaId}/cast-proposal/apply`, { people }, f)
