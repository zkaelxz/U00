// Pure helpers for the assistant's tier ladder: which tier answered, what
// the next one is, and exactly what asking it would send. The page never
// escalates on its own; these only describe the offer the user confirms.
import { MAX_HISTORY_TURNS } from '../../api/assistant'
import { humanize } from '../../components/labels'
import type { ChatTurn, ReportRequest } from '../../types/assistant'
import { historyOf, type Exchange } from './assistantFormat'

export type FailureReason = 'rate_limited' | 'unreachable' | 'unavailable' | 'spend_cap' | 'failed'

/** A tier that could not answer, as the API describes it (never a key or path). */
export interface TierFailure {
  reason: FailureReason
  engine: string
  tier: number | null
  nextEngine: string | null
}

const REASONS: FailureReason[] = ['rate_limited', 'unreachable', 'unavailable', 'spend_cap', 'failed']

export function tierFailureOf(e: unknown): TierFailure | null {
  const d = (e as { details?: unknown } | null)?.details as Record<string, unknown> | undefined
  if (!d || typeof d !== 'object' || !REASONS.includes(d.reason as FailureReason) || typeof d.engine !== 'string') return null
  return {
    reason: d.reason as FailureReason,
    engine: d.engine,
    tier: typeof d.tier === 'number' ? d.tier : null,
    nextEngine: typeof d.next_engine === 'string' && d.next_engine ? d.next_engine : null,
  }
}

export function failureText(f: TierFailure): string {
  const name = engineName(f.engine)
  switch (f.reason) {
    case 'rate_limited':
      return `${name} is busy or over its rate limit. Wait a minute and ask again, or ask the next tier.`
    case 'unreachable':
      return f.engine === 'ollama'
        ? 'Ollama didn’t answer. Check that Ollama is running on this PC, then ask again.'
        : `${name} didn’t answer. Check your internet connection, then ask again.`
    case 'unavailable':
      return `${name} isn’t set up here (no API key, or its package isn’t installed). Add a key in Settings, or ask another tier.`
    case 'spend_cap':
      return `This month’s spending cap is used up, so ${name} wasn’t asked.`
    default:
      return `${name} couldn’t answer this time.`
  }
}

export function engineName(engine: string): string {
  return humanize('engine', engine)
}

/** "Tier 1 · Ollama · on this PC"; an engine outside the ladder has no tier. */
export function tierLabel(tier: number | null | undefined, engine: string, local?: boolean): string {
  const parts = [tier ? `Tier ${tier}` : null, engineName(engine), local ? 'on this PC' : 'cloud']
  return parts.filter(Boolean).join(' · ')
}

/** The tier the user may ask after this exchange, or null. */
export function nextTierOf(x: Exchange): string | null {
  if (x.response) return x.response.next_engine || null
  return x.failure?.nextEngine ?? null
}

/** What asking the next tier about exchange `index` sends: its question, the chat before it and the evidence. */
export interface EscalationPlan {
  question: string
  history: ChatTurn[]
  evidence: string
}

export function escalationPlan(exchanges: Exchange[], index: number): EscalationPlan {
  const x = exchanges[index]
  return {
    question: x.question,
    history: historyOf(exchanges.slice(0, index)).slice(-MAX_HISTORY_TURNS),
    evidence: x.response?.evidence ?? '',
  }
}

/** The dialog's plain list of exactly what will be sent. */
export function sendSummary(plan: EscalationPlan): string[] {
  const n = plan.history.length
  return [
    'Your question.',
    n ? `The last ${n} ${n === 1 ? 'message' : 'messages'} of this chat.` : 'No earlier messages (this is the first question).',
    plan.evidence
      ? `What the earlier tiers’ read-only tools found (${plan.evidence.length.toLocaleString()} characters, redacted).`
      : 'No tool output (the earlier tiers gathered none).',
  ]
}

/** Provider-specific privacy note for the confirm dialog. */
export function privacyNote(engine: string, local: boolean): string {
  if (local) return 'This tier runs on this PC: nothing leaves it.'
  const base = `This leaves your PC and goes to ${engineName(engine)}. It can also read more of this app’s code and redacted logs with the same read-only tools. Keys, tokens and this PC’s folder names are removed first.`
  if (engine === 'gemini') {
    return `${base} On Gemini’s free tier, Google may use what you send to improve its products, and people may read it.`
  }
  return base
}

/** The developer report's input: the chat, a last unanswered question, and the latest tool output. */
export function reportRequest(exchanges: Exchange[]): ReportRequest {
  const last = exchanges[exchanges.length - 1]
  const withEvidence = [...exchanges].reverse().find((x) => x.response?.evidence)
  return {
    chat_history: historyOf(exchanges).slice(-MAX_HISTORY_TURNS),
    question: last && !last.response ? last.question : undefined,
    evidence: withEvidence?.response?.evidence ?? '',
  }
}
