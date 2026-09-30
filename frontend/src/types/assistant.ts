// Step 42: the in-app AI maintenance assistant, read-only v1
// (api/routers/assistant_routes.py). Every route is PC only.

export interface AssistantSettings {
  developer_mode: boolean
  // null: the server's default engine / the engine's default model.
  engine: string | null
  model: string | null
  engine_choices: string[]
  // Step 60: implement -> independent review on a different engine. Off by default.
  roles_enabled?: boolean
  review_engine?: string | null
  review_model?: string | null
  // Engines the review role may use (no offline test engine).
  review_engine_choices?: string[]
  // The engine used when none is picked or saved (local: Ollama).
  default_engine?: string
  // Engines that run on this PC; code and logs never leave it.
  local_engines?: string[]
  // Per cloud engine: has the owner allowed sending code and logs to it?
  cloud_consent?: Record<string, boolean>
}

export type AssistantSettingsPatch = Partial<
  Pick<AssistantSettings, 'developer_mode' | 'engine' | 'model' | 'roles_enabled' | 'review_engine' | 'review_model' | 'cloud_consent'>
>

export interface AssistantTool {
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
}

export interface ProposedPatch {
  id: string
  patch: string
  files: string[]
}

export type BacklogKind = 'bug' | 'feature' | 'note'

export interface SuggestedBacklogItem {
  kind: BacklogKind
  text: string
}

export interface ToolCall {
  id: string
  name: string
  args: Record<string, unknown>
  ok: boolean
  summary: string
}

export type ReviewVerdict = 'agrees' | 'concerns' | 'unclear' | 'unavailable'

// Step 60: the independent review role's view of the proposed fix.
export interface AssistantReview {
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
