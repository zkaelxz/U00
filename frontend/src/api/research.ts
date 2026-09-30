import { getJson, postJson } from './client'
import type {
  ProvenanceList, ResearchApplied, ResearchBudget, ResearchChoice, ResearchRequest, ResearchResult,
} from '../types/research'

type Fetch = typeof fetch

const base = (id: number) => `/api/metadata/dramas/${id}`

export const getResearchBudget = (f: Fetch = fetch) =>
  getJson<ResearchBudget>('/api/metadata/research/budget', f)

// Read-only: the sources stored when earlier research was applied.
export const getResearchProvenance = (id: number, f: Fetch = fetch) =>
  getJson<ProvenanceList>(`${base(id)}/provenance`, f)

// Writes nothing: returns researched values with their sources.
export const researchMetadata = (id: number, req: ResearchRequest, f: Fetch = fetch) =>
  postJson<ResearchResult>(`${base(id)}/research`, req, f)

// The server takes values from the stored result; only the choices are sent,
// with the drama's values the user was shown (a 409 if they changed since).
export const applyResearch = (
  id: number, researchId: string, choices: Record<string, ResearchChoice>,
  seen: Record<string, string | null>, f: Fetch = fetch,
) => postJson<ResearchApplied>(`${base(id)}/research/apply`, { research_id: researchId, choices, seen }, f)
