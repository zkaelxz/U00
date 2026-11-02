// Mirrors api/schemas/timing_check.py, from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

// A flagged line's corrected times; start/end are the times the check saw.
export type TimingSuggestion = Schemas['TimingCheckSuggestion']
export type TimingCheckStatus = Schemas['TimingCheckStatus']
export type TimingSnapResult = Schemas['TimingSnapResult']
