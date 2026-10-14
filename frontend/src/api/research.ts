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

// The earlier identical lookup from the server's cache (404 = none); never runs a search.
export const getCachedResearch = (id: number, mode: string, model: string, f: Fetch = fetch) =>
  getJson<ResearchResult>(`${base(id)}/research/cached?mode=${encodeURIComponent(mode)}&model=${encodeURIComponent(model)}`, f)

// The server takes values from the stored result; only the choices are sent,
// with the drama's values the user was shown (a 409 if they changed since).
// A result from the cached read has no research_id: the server snapshots that lookup now,
// if it is still the one shown (mode, model and retrieved_at identify it).
export const applyResearch = (
  id: number, shown: Pick<ResearchResult, 'research_id' | 'mode' | 'model' | 'retrieved_at'>,
  choices: Record<string, ResearchChoice>, seen: Record<string, string | null>, f: Fetch = fetch,
) => postJson<ResearchApplied>(
  `${base(id)}/research/apply`,
  shown.research_id
    ? { research_id: shown.research_id, choices, seen }
    : { lookup_mode: shown.mode, lookup_model: shown.model, retrieved_at: shown.retrieved_at, choices, seen },
  f,
)
