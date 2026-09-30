// Step 42: the in-app AI maintenance assistant, read-only v1
// (api/routers/assistant_routes.py). Every route is PC only.

export interface AssistantSettings {
  developer_mode: boolean
  // null: the server's default engine / the engine's default model.
  engine: string | null
  model: string | null
  engine_choices: string[]
}

export type AssistantSettingsPatch = Partial<Pick<AssistantSettings, 'developer_mode' | 'engine' | 'model'>>

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

export interface AskResponse {
  answer: string
  proposed_patches: ProposedPatch[]
  suggested_backlog: SuggestedBacklogItem[]
  tool_calls: ToolCall[]
  engine: string
  model: string | null
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
