// Mirrors api/metadata_research_schemas.py (grounded research).
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type ResearchMode = 'quick' | 'deep' | 'verify'
export type ResearchChoice = 'keep' | 'replace' | 'save_both' | 'confirm'

export type ResearchField = Schemas['ResearchField']

// per_model was optional before the types were generated and fixtures omit it, so it stays optional.
export type ResearchBudget = Omit<Schemas['ResearchBudget'], 'per_model'> & Partial<Pick<Schemas['ResearchBudget'], 'per_model'>>

export type ResearchResult = Schemas['ResearchResult']

export type ResearchRequest = Schemas['ResearchRequest']

// One stored per-field evidence row. The API also returns source URLs; the
// note shows source titles only.
export type ProvenanceRow = Schemas['ProvenanceRow']

export type ProvenanceList = Schemas['ProvenanceList']

export type ResearchApplied = Schemas['ResearchApplied']
