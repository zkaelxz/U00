import type { ApiError } from '../api/client'
import { capFirst, engineLabel } from '../labels'

// Plain-language text per stable API error code (api/error_handlers.py).
// The server's own message is shown as extra detail only for codes where
// it is written for people, and anything path- or key-like is dropped.
const GENERIC: Record<string, string> = {
  validation_error: 'Some of the values entered are not valid. Check them and try again.',
  invalid_input: 'Some of the values entered are not valid. Check them and try again.',
  not_found: 'That item could not be found. It may have been deleted.',
  conflict: 'That cannot be done right now because something else is already using it.',
  unsupported_operation: 'This project type does not support that action.',
  dependency_unavailable:
    'A tool or package this needs is not installed or not reachable. See Diagnostics.',
  application_error: 'The action failed. Nothing was changed unless stated otherwise.',
  internal_error: 'Something went wrong inside Baihe. Details are in the app log.',
  network_error: 'Could not reach the Baihe API. Is it running?',
  forbidden: 'Not allowed from this device or account.',
  unauthenticated: 'Your session has ended. Sign in again.',
  rate_limited: 'Too many requests. Wait a moment and try again.',
}

// A PC-only call refused with 403 (the viewer is not at the main PC).
export const PC_ONLY_FORBIDDEN = 'This only works on the main PC.'

// The one message for a refused key, token or address save (403).
export const KEY_WRITES_REFUSED =
  'This can only be changed on the Baihe PC itself, with key writes turned on. start.bat turns them on; if you started the API another way, set BAIHE_API_ALLOW_KEY_WRITES=1.'

export interface DescribeOptions {
  // PC-only callers: a 403 reads PC_ONLY_FORBIDDEN instead of the generic text.
  pcOnly?: boolean
  // Admin/restore callers: validation_error and invalid_input show the
  // server's own text too (fixed sentences, no paths; still safeDetail-filtered).
  serverText?: boolean
  // Refusals whose server sentence is the whole answer ("There is no dub yet...")
  // show it as the heading instead of the generic validation text.
  reasonAsTitle?: boolean
}

const SERVER_TEXT_CODES = ['not_found', 'conflict', 'unsupported_operation', 'dependency_unavailable']

const SENSITIVE =
  /(?:[A-Za-z]:\\|\\\\|\/(?:home|Users|tmp|var|etc|root|opt|mnt)\/|\bsk-[\w-]{6,}|\bBearer\s+\S+|(?:api[_-]?key|token|secret)\s*[=:])/i

export function safeDetail(message: string): string | null {
  const m = message.trim()
  if (!m || m.length > 200 || SENSITIVE.test(m)) return null
  return m
}

const OPT_IN_SERVER_TEXT_CODES = ['validation_error', 'invalid_input']

export function describeError(
  err: unknown,
  opts: DescribeOptions = {},
): { title: string; detail: string | null } {
  const e = err as Partial<ApiError> | null
  const code = e?.code ?? 'internal_error'
  // Ollama failures carry a fixed, user-fixable sentence of their own.
  const reason = (e?.details as { reason?: unknown } | undefined)?.reason
  const ollamaText =
    code === 'dependency_unavailable' && typeof reason === 'string' && reason.startsWith('ollama_')
      ? safeDetail(e?.message ?? '')
      : null
  // A missing key is not a missing package: it has its own heading.
  const missingKey = code === 'dependency_unavailable' && reason === 'no_key'
  const engine = (e?.details as { engine?: unknown } | undefined)?.engine
  const keyTitle = `No key is set for ${typeof engine === 'string' && /^[\w-]{1,40}$/.test(engine) ? engineLabel(engine) : 'this engine'}. Add it in Settings.`
  const refusalText =
    opts.reasonAsTitle && OPT_IN_SERVER_TEXT_CODES.includes(code) && e?.message ? safeDetail(e.message) : null
  const title =
    opts.pcOnly && (code === 'forbidden' || e?.status === 403)
      ? PC_ONLY_FORBIDDEN
      : (ollamaText ?? (missingKey ? keyTitle : null) ?? refusalText ?? GENERIC[code] ?? GENERIC.application_error)
  const showServer =
    SERVER_TEXT_CODES.includes(code) || (opts.serverText && OPT_IN_SERVER_TEXT_CODES.includes(code))
  const rawDetail = e?.message && showServer && !ollamaText && !missingKey && !refusalText ? safeDetail(e.message) : null
  const detail = rawDetail ? capFirst(rawDetail) : null
  return { title, detail }
}

export type EngineFailureKind = 'not_running' | 'no_model' | 'key_rejected' | 'rate_limited' | 'timed_out' | 'unreachable' | 'other'

// Engines that run on this PC (or a server the user runs); a refused connection means "not started".
const LOCAL_ENGINES = new Set(['ollama'])

/** A one-sentence, plain summary of an engine test failure; the raw text stays as the "Details". */
export function summarizeEngineFailure(
  engine: string,
  raw: string | null | undefined,
  engineLabel: string = engine,
): { kind: EngineFailureKind; summary: string } {
  const text = raw ?? ''
  const local = LOCAL_ENGINES.has(engine)
  if (/\b(401|403)\b|unauthori[sz]ed|forbidden|invalid[\s_-]*(api[\s_-]*)?key|permission denied|authentication/i.test(text)) {
    return { kind: 'key_rejected', summary: `${engineLabel} rejected the key. Check its key under Engines and keys, then test again.` }
  }
  if (/\b429\b|rate[\s_-]*limit|too many requests|quota/i.test(text)) {
    return { kind: 'rate_limited', summary: `${engineLabel} is rate limited. Wait a minute, then test again.` }
  }
  if (/\b404\b.*model|model.*(not found|not\s+exist|pull)|try pulling/i.test(text) && engine === 'ollama') {
    return {
      kind: 'no_model',
      summary: "Ollama is running but doesn't have that model. Pull one with `ollama pull <model>`, then test again.",
    }
  }
  if (/no answer within|timed? ?out|timeout/i.test(text)) {
    return { kind: 'timed_out', summary: `${engineLabel} timed out. Try again in a moment.` }
  }
  if (/10061|refused|econnrefused|max retries exceeded|failed to establish|connection (aborted|error)|name or service not known|getaddrinfo/i.test(text)) {
    if (engine === 'ollama') {
      return {
        kind: 'not_running',
        summary:
          "Ollama isn't running. Install it from ollama.com (Baihe doesn't install it) and start the Ollama app, pull a model, then test again.",
      }
    }
    return local
      ? { kind: 'not_running', summary: `${engineLabel} isn't running. Start it, then test again.` }
      : { kind: 'unreachable', summary: `Couldn't reach ${engineLabel}. Check your internet connection, then test again.` }
  }
  return { kind: 'other', summary: `The ${engineLabel} test failed.` }
}
