// The AI maintenance assistant (Step 42, api/routers/assistant_routes.py).
// Every route is PC only: from another device they answer 403. Reads use a
// plain fetch (the caller treats a 403 as "PC only" and hides the feature);
// writes go through pcOnlyFetch like the other PC-only calls. Only the
// fields that changed or were picked are sent; blank engine/model are left
// out so the server uses its default.
import type {
  AskRequest,
  AskResponse,
  AssistantSettings,
  AssistantSettingsPatch,
  AssistantToolList,
  BacklogItem,
  BacklogKind,
  BacklogList,
  ChangelogRequest,
  ChangelogResponse,
  ChatTurn,
  ReportRequest,
  ReportResponse,
} from '../types/assistant'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/assistant'

// The API's input limits (it answers 422 past them).
export const MAX_QUESTION = 4000
export const MAX_HISTORY_TURNS = 20
export const MAX_BACKLOG_TEXT = 1000
const MAX_EVIDENCE = 16000

export const getAssistantSettings = (f?: Fetch) => getJson<AssistantSettings>(`${BASE}/settings`, f)

export const saveAssistantSettings = (patch: AssistantSettingsPatch, f?: Fetch) =>
  postJson<AssistantSettings>(`${BASE}/settings`, patch, pcOnlyFetch(f))

export const getAssistantTools = (f?: Fetch) => getJson<AssistantToolList>(`${BASE}/tools`, f)

/** engine/model only when picked (blank means "server default"). */
function engineFields(engine?: string | null, model?: string | null): { engine?: string; model?: string } {
  const out: { engine?: string; model?: string } = {}
  if (engine?.trim()) out.engine = engine.trim()
  if (model?.trim()) out.model = model.trim()
  return out
}

/** The last MAX_HISTORY_TURNS turns, oldest first. */
export function trimHistory(history: ChatTurn[]): ChatTurn[] {
  return history.slice(-MAX_HISTORY_TURNS)
}

/** An escalation to a higher tier: sent only after the user confirmed it. */
export interface Escalation {
  consent: boolean
  evidence: string
}

export function askAssistant(
  question: string,
  history: ChatTurn[],
  engine?: string | null,
  model?: string | null,
  f?: Fetch,
  escalation?: Escalation,
): Promise<AskResponse> {
  const body: AskRequest = { question, chat_history: trimHistory(history), ...engineFields(engine, model) }
  if (escalation) {
    body.escalate = true
    body.consent = escalation.consent
    if (escalation.evidence) body.evidence = escalation.evidence.slice(0, MAX_EVIDENCE)
  }
  return postJson<AskResponse>(`${BASE}/ask`, body, pcOnlyFetch(f))
}

/** The redacted problem report for a developer; built on the PC, never uploaded. */
export function prepareDeveloperReport(req: ReportRequest, f?: Fetch): Promise<ReportResponse> {
  const body: ReportRequest = { chat_history: trimHistory(req.chat_history) }
  if (req.question) body.question = req.question
  if (req.evidence) body.evidence = req.evidence.slice(0, MAX_EVIDENCE)
  return postJson<ReportResponse>(`${BASE}/report`, body, pcOnlyFetch(f))
}

export function generateChangelog(
  fromRef: string,
  toRef?: string,
  engine?: string | null,
  model?: string | null,
  f?: Fetch,
): Promise<ChangelogResponse> {
  const body: ChangelogRequest = { from_ref: fromRef.trim() }
  if (toRef?.trim()) body.to_ref = toRef.trim()
  return postJson<ChangelogResponse>(`${BASE}/changelog`, { ...body, ...engineFields(engine, model) }, pcOnlyFetch(f))
}

export const listBacklog = (f?: Fetch) => getJson<BacklogList>(`${BASE}/backlog`, f)

export const addBacklogItem = (kind: BacklogKind, text: string, f?: Fetch) =>
  postJson<BacklogItem>(`${BASE}/backlog`, { kind, text }, pcOnlyFetch(f))

export const deleteBacklogItem = (id: number, f?: Fetch) =>
  postJson<{ deleted: boolean }>(`${BASE}/backlog/${id}/delete`, { confirm: true }, pcOnlyFetch(f))

export const clearBacklog = (f?: Fetch) =>
  postJson<{ deleted: number }>(`${BASE}/backlog/clear`, { confirm: true }, pcOnlyFetch(f))
