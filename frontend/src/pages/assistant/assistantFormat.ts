// Pure helpers for the Maintenance assistant page (kept out of the .tsx for tests).
import { ApiError } from '../../api/client'
import { describeError, safeDetail } from '../../components/errorMessages'
import { humanize, type BadgeTone } from '../../components/labels'
import type { AskResponse, AssistantSettings, AssistantSettingsPatch, BacklogKind, ChatTurn } from '../../types/assistant'
import type { TierFailure } from './escalation'

export const DEVELOPER_MODE_HELP = 'Shows the AI maintenance assistant in the menu. Off by default.'
export const PC_ONLY_TEXT = 'The maintenance assistant is available on the PC only.'
export const MODE_OFF_TEXT = 'Developer Mode is off. Turn it on to use the assistant.'
export const NO_KEY_TEXT = 'No API key is set for that engine. Add one in Settings, or pick another engine.'
export const PATCH_LABEL = 'Proposed fix — not applied. Review it and apply it by hand.'
export const READ_ONLY_NOTE = 'Read-only: this assistant has no tool that changes files, git or settings.'

export const BACKLOG_KINDS: { kind: BacklogKind; label: string; tone: BadgeTone }[] = [
  { kind: 'bug', label: 'Bug', tone: 'warn' },
  { kind: 'feature', label: 'Feature', tone: 'info' },
  { kind: 'note', label: 'Note', tone: 'neutral' },
]

export function kindLabel(kind: BacklogKind): { label: string; tone: BadgeTone } {
  return BACKLOG_KINDS.find((k) => k.kind === kind) ?? { label: 'Note', tone: 'neutral' }
}

export function isForbidden(e: unknown): boolean {
  return e instanceof ApiError ? e.status === 403 : (e as { status?: number } | null)?.status === 403
}

/** Plain text for an assistant call's failure. Server text only when it is safe (no paths or keys). */
/** The engine a 409 names when it needs the owner's consent to send code and logs, else null. */
export function consentEngineOf(e: unknown): string | null {
  const err = e as Partial<ApiError> | null
  const d = err?.details as { reason?: string; engine?: unknown } | undefined
  if (err?.status !== 409 || d?.reason !== 'cloud_consent_required') return null
  return typeof d.engine === 'string' ? d.engine : ''
}

function consentNeededText(engine: string): string {
  const name = engine ? humanize('engine', engine) : 'that engine'
  return `Sending this app's code and logs to ${name} isn't allowed yet. Allow it below (Engine), or use Ollama to keep everything on this PC.`
}

/** Cloud engines the owner hasn't allowed yet; a local engine never needs it. */
export function needsConsent(s: Pick<AssistantSettings, 'cloud_consent' | 'local_engines' | 'default_engine'>, engine: string): boolean {
  const e = engine || s.default_engine || 'ollama'
  if ((s.local_engines ?? ['ollama']).includes(e)) return false
  return s.cloud_consent?.[e] !== true
}

export function assistantErrorText(e: unknown): string {
  const err = e as Partial<ApiError> | null
  const status = err?.status
  const consent = consentEngineOf(e)
  if (consent !== null) return consentNeededText(consent)
  if (status === 409 && (err?.details as { reason?: string } | undefined)?.reason === 'escalation_consent_required') {
    return 'Asking a cloud tier needs your OK each time. Use “Ask a stronger model” and confirm.'
  }
  if (status === 403 || err?.code === 'forbidden') return PC_ONLY_TEXT
  if (status === 409) return MODE_OFF_TEXT
  if (status === 503) return NO_KEY_TEXT
  if (status === 422 || err?.code === 'validation_error' || err?.code === 'invalid_input') {
    const detail = err?.message ? safeDetail(err.message) : null
    return detail ?? 'Some of the values entered are not valid. Check them and try again.'
  }
  const { title, detail } = describeError(e, { serverText: true })
  return detail ? `${title} ${detail}` : title
}

/** One exchange in the chat: the question, then the answer or an error. */
export interface Exchange {
  id: number
  question: string
  response: AskResponse | null
  error: string | null
  // Set when a tier could not answer: which one, and which tier the user may ask next.
  failure?: TierFailure | null
}

/** chat_history for the next ask: each answered question and its answer text only. */
export function historyOf(exchanges: Exchange[]): ChatTurn[] {
  const out: ChatTurn[] = []
  for (const x of exchanges) {
    if (!x.response) continue
    out.push({ role: 'user', content: x.question }, { role: 'assistant', content: x.response.answer })
  }
  return out
}

/** Tool arguments on one line, cut to `max` characters. */
export function compactArgs(args: Record<string, unknown> | null | undefined, max = 160): string {
  let s: string
  try {
    s = JSON.stringify(args ?? {}) ?? '{}'
  } catch {
    s = '{…}'
  }
  return s.length > max ? `${s.slice(0, max - 1)}…` : s
}

/** "2026-09-29T12:00:00" -> a short local date and time; unparseable text is shown as sent. */
export function shortDate(iso: string): string {
  const d = new Date(iso)
  if (!iso || Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/** Only the engine/model fields that differ from the saved settings; blank means "server default" (null). */
export function engineChanges(
  saved: Pick<AssistantSettings, 'engine' | 'model'>,
  engine: string,
  model: string,
): AssistantSettingsPatch {
  const next = { engine: engine.trim() || null, model: model.trim() || null }
  const out: AssistantSettingsPatch = {}
  if (next.engine !== (saved.engine || null)) out.engine = next.engine
  if (next.model !== (saved.model || null)) out.model = next.model
  return out
}

/** "3 tools used" style counts. */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}
