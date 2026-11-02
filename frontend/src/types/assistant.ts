// The in-app AI maintenance assistant, read-only v1
// (api/routers/assistant_routes.py). Every route is PC only.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type AssistantSettings = Schemas['AssistantSettings']

export type AssistantSettingsPatch = Partial<
  Pick<AssistantSettings, 'developer_mode' | 'engine' | 'model' | 'roles_enabled' | 'review_engine' | 'review_model' | 'cloud_consent'>
> & { tiers?: string[] | null }

interface AssistantTool {
  name: string
  description: string
  tier: 'green'
}

export interface AssistantToolList {
  tools: AssistantTool[]
  // Always empty in this build: nothing the assistant runs can change files, git or settings.
  write_tools: string[]
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface AskRequest {
  question: string
  chat_history: ChatTurn[]
  engine?: string
  model?: string
  // Re-asking on a higher tier: consent must be true for a tier that leaves this PC.
  escalate?: boolean
  consent?: boolean
  // The previous tier's redacted tool output.
  evidence?: string
}

export interface ReportRequest {
  chat_history: ChatTurn[]
  question?: string
  evidence?: string
}

export interface ReportResponse {
  report: string
}

interface ProposedPatch {
  id: string
  patch: string
  files: string[]
}

export type BacklogKind = 'bug' | 'feature' | 'note'

export interface SuggestedBacklogItem {
  kind: BacklogKind
  text: string
}

interface ToolCall {
  id: string
  name: string
  args: Record<string, unknown>
  ok: boolean
  summary: string
}

export type ReviewVerdict = 'agrees' | 'concerns' | 'unclear' | 'unavailable'

// The independent review role's view of the proposed fix.
interface AssistantReview {
  engine: string | null
  model: string | null
  verdict: ReviewVerdict
  notes: string
  tool_calls: ToolCall[]
}

export interface AskResponse {
  answer: string
  proposed_patches: ProposedPatch[]
  suggested_backlog: SuggestedBacklogItem[]
  tool_calls: ToolCall[]
  engine: string
  model: string | null
  review?: AssistantReview | null
  // Why a proposed fix got no review (an escalation never sends it to an unconfirmed cloud reviewer).
  review_skipped?: string
  // Which tier answered (null: an engine outside the ladder) and whether it ran on this PC.
  tier?: number | null
  local?: boolean
  // The tier the user may ask next, if any.
  next_engine?: string | null
  // Redacted tool output an escalation or a developer report would carry.
  evidence?: string
}

export interface ChangelogRequest {
  from_ref: string
  to_ref?: string
  engine?: string
  model?: string
}

export interface ChangelogResponse {
  changelog: string
  commit_count: number
  from_ref: string
  to_ref: string
  // True when the range had more commits than the model was shown (the newest ones were).
  truncated?: boolean
}

export interface BacklogItem {
  id: number
  kind: BacklogKind
  text: string
  created_at: string
}

export interface BacklogList {
  items: BacklogItem[]
}
