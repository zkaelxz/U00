import type { ApiError } from '../api/client'
import { getPcMode } from '../api/pcOnly'
import { getSession } from '../hooks/useSession'
import { capFirst, engineLabel } from '../labels'

// Plain-language text per stable API error code (api/error_handlers.py).
// The server's own message is shown as extra detail only for codes where
// it is written for people, and anything path- or key-like is dropped.
// Who is reading the error decides whether "see Diagnostics" or "ask the PC owner" is useful.
// Unknown (/me not answered, auth off) reads as an admin, as useHolds does: the server still refuses.
export const ASK_PC_OWNER = 'Ask the person who runs Baihe on the PC.'

function canOpenDiagnostics(): boolean {
  const s = getSession()
  return s.status !== 'ready' || !s.me.auth_enabled || s.me.permissions.includes('admin.diagnostics')
}

const DEPENDENCY_ADMIN = 'A tool or package this needs is not installed or not reachable. See Diagnostics.'
const DEPENDENCY_MEMBER = `A tool or package this needs is not installed or not reachable. ${ASK_PC_OWNER}`

export function couldNotReachBaihe(): string {
  if (getPcMode() === 'remote' || !canOpenDiagnostics()) {
    return `Could not reach Baihe. Check your connection, or ask the person who runs Baihe on the PC.`
  }
  return 'Could not reach Baihe. Make sure it is still running on this PC.'
}

// A stale sign-in: the session ended or its CSRF token no longer matches.
export const SESSION_ENDED = 'Your session has ended. Sign in again.'

const GENERIC: Record<string, string> = {
  validation_error: 'Some of the values entered are not valid. Check them and try again.',
  invalid_input: 'Some of the values entered are not valid. Check them and try again.',
  not_found: 'That item could not be found. It may have been deleted.',
  conflict: 'That cannot be done right now because something else is already using it.',
  unsupported_operation: 'This project type does not support that action.',
  application_error: 'The action failed. Nothing was changed unless stated otherwise.',
  job_failed: 'The job failed.',
  internal_error: 'Something went wrong inside Baihe. Details are in the app log.',
  forbidden: 'Not allowed from this device or account.',
  model_not_downloaded: 'This model is not downloaded yet. Ask the owner of the main PC to download it first.',
  unauthenticated: SESSION_ENDED,
  rate_limited: 'Too many requests. Wait a moment and try again.',
  timeout:
    'The server did not answer in time. The change may not have been saved; check before trying again.',
  extension_only:
    'This site only works through the browser extension. Open the chapter in Chrome and use the extension.',
}

// A failed read changed nothing, so it must not say "Nothing was changed".
const READ_GENERIC: Record<string, string> = {
  application_error: 'Could not load this. Try again.',
  internal_error: 'Could not load this. Something went wrong inside Baihe; details are in the app log.',
  timeout: 'Could not load this: the server took too long to answer. Try again.',
}

// FastAPI's own 422 body carries this placeholder instead of a reason.
const PLACEHOLDER_INVALID = 'The request is invalid.'

// A PC-only call refused with 403 (the viewer is not at the main PC).
export const PC_ONLY_FORBIDDEN = 'This only works on the main PC.'

// A call that returns line text refused with 403: the account may use the
// panel but not read lines, so it gets a plain note, not an error banner.
export const LINES_READ_FORBIDDEN = 'Seeing line text needs permission to read lines. Ask the owner of the main PC.'

// A model refusal is a 403 too, but its sentence says what to do, so it is
// not read as "this device or account may not".
export const MODEL_NOT_DOWNLOADED = 'model_not_downloaded'

export function isForbidden(err: unknown): boolean {
  const e = err as { status?: number; code?: string } | null
  if (e?.code === MODEL_NOT_DOWNLOADED) return false
  return e?.status === 403 || e?.code === 'forbidden'
}

// The message for a refused key, token or address save (403). The environment variable and .bat hint
// is for people who can act on it; everyone else is sent to the person who runs Baihe.
export function keyWritesRefused(): string {
  const s = getSession()
  const atPc = getPcMode() === 'local'
  const holdsSettings = s.status === 'ready' && (!s.me.auth_enabled || s.me.permissions.includes('admin.settings'))
  if (s.status === 'ready' && !holdsSettings && !atPc) return ASK_PC_OWNER
  const base = 'Change this on the Baihe PC with key writes on.'
  return atPc || holdsSettings
    ? `${base} Details: start.bat turns them on; otherwise set BAIHE_API_ALLOW_KEY_WRITES=1.`
    : base
}

export interface DescribeOptions {
  // PC-only callers: a 403 reads PC_ONLY_FORBIDDEN instead of the generic text.
  pcOnly?: boolean
  // Admin/restore callers: validation_error and invalid_input show the
  // server's own text too (fixed sentences, no paths; still safeDetail-filtered).
  serverText?: boolean
  // Refusals whose server sentence is the whole answer ("There is no dub track for this title.")
  // show it as the heading instead of the generic validation text.
  reasonAsTitle?: boolean
}

// A package the server named in `details` (fixed text; no path or traceback): install it, don't add a key.
export const MISSING_PACKAGE_TITLE = "A package this needs isn't installed. Install it in Diagnostics."
const MISSING_PACKAGE_MEMBER = `A package this needs isn't installed. ${ASK_PC_OWNER}`

const SERVER_TEXT_CODES = ['not_found', 'conflict', 'unsupported_operation', 'dependency_unavailable']

const SENSITIVE =
  /(?:[A-Za-z]:\\|\\\\|\/(?:home|Users|tmp|var|etc|root|opt|mnt)\/|\bsk-[\w-]{6,}|\bBearer\s+\S+|(?:api[_-]?key|token|secret)\s*[=:])/i

export function safeDetail(message: string): string | null {
  const m = message.trim()
  if (!m || m.length > 200 || SENSITIVE.test(m)) return null
  return m
}

const OPT_IN_SERVER_TEXT_CODES = ['validation_error', 'invalid_input']

// Reasons an engine call names with a fixed sentence written for people
// (services/engine_failure_service.py, services/metadata_research_service.py).
const ENGINE_REASONS = [
  'daily_limit', 'key_rejected', 'rate_limited', 'timed_out', 'unavailable', 'unreachable', 'blocked', 'failed',
  'truncated', 'model_unavailable', 'refused', 'model_used_up', 'searches_used_up',
]

// Disk, file and database problems the server names with a fixed sentence
// (api/error_handlers.py); the generic "The action failed" would hide the cause.
const STORAGE_CODES = [
  'disk_full', 'library_read_only', 'database_corrupt', 'permission_denied', 'file_in_use', 'library_busy',
  'sources_db_busy',
]

// A background job that ended in error, wrapped by its caller: the job's text is the
// only reason the person gets, and the server writes it after the exception's class name.
export const JOB_FAILED = 'job_failed'
// A job that followed another (an upload's transcription) wraps its error in a second one.
const EXCEPTION_PREFIX = /^(?:[A-Z]\w*(?:Error|Exception): )+/
// The same class names anywhere in a text: a step's reason is written after "Download stopped: ", not at its start.
const EXCEPTION_NAMES = /\b[A-Z]\w*(?:Error|Exception): /g

/** Server text for people, without Python exception class names ("RuntimeError: "). */
export const withoutExceptionNames = (text: string): string => text.replace(EXCEPTION_NAMES, '')

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
  // A missing key or token is not a missing package: each has its own heading and fix.
  // A link whose address would not resolve is a typo or a connection problem, never a missing package.
  const addressText =
    code === 'dependency_unavailable' && reason === 'address_unresolved' ? safeDetail(e?.message ?? '') : null
  const missingToken = code === 'dependency_unavailable' && reason === 'no_token'
  const missingKey = code === 'dependency_unavailable' && (reason === 'no_key' || missingToken)
  const missingPackage = code === 'dependency_unavailable' && reason === 'missing_package'
  const engine = (e?.details as { engine?: unknown } | undefined)?.engine
  // An engine that answered with its own reason (a used-up quota, a refused key) is not a
  // missing package either: the server's fixed sentence for that reason is the heading.
  const engineSentence =
    code === 'dependency_unavailable' && typeof reason === 'string' && ENGINE_REASONS.includes(reason) && typeof engine === 'string'
      ? safeDetail(e?.message ?? '')
      : null
  // The sentence may send the reader to Diagnostics, which a member cannot open.
  const engineText = engineSentence && (canOpenDiagnostics() || !/Diagnostics/.test(engineSentence)) ? engineSentence : null
  const keyTitle = missingToken
    ? `No ${typeof engine === 'string' && /^[\w -]{1,40}$/.test(engine) ? engine : 'access'} token is set. Add it in Settings.`
    : `No key is set for ${typeof engine === 'string' && /^[\w-]{1,40}$/.test(engine) ? engineLabel(engine) : 'this engine'}. Add it in Settings.`
  const refusalText =
    opts.reasonAsTitle && OPT_IN_SERVER_TEXT_CODES.includes(code) && e?.message ? safeDetail(e.message) : null
  const isRead = e?.method === 'GET' || e?.method === 'HEAD'
  const jobText = code === JOB_FAILED && typeof e?.message === 'string' ? safeDetail(e.message.replace(EXCEPTION_PREFIX, '')) : null
  // 422 sentences (InvalidInputError) and 429 wait times are fixed server
  // text written for people; safeDetail still drops anything path- or key-like.
  const serverMsg = typeof e?.message === 'string' ? safeDetail(e.message) : null
  const serverTitle =
    serverMsg &&
    serverMsg !== PLACEHOLDER_INVALID &&
    ((e?.status === 422 && OPT_IN_SERVER_TEXT_CODES.includes(code)) || code === 'rate_limited' ||
      STORAGE_CODES.includes(code))
      ? capFirst(serverMsg)
      : null
  const title =
    opts.pcOnly && code !== MODEL_NOT_DOWNLOADED && (code === 'forbidden' || e?.status === 403)
      ? PC_ONLY_FORBIDDEN
      : ((jobText ? capFirst(jobText) : null) ??
        ollamaText ??
        addressText ??
        engineText ??
        (missingKey ? keyTitle : null) ??
        (missingPackage ? (canOpenDiagnostics() ? MISSING_PACKAGE_TITLE : MISSING_PACKAGE_MEMBER) : null) ??
        refusalText ??
        serverTitle ??
        (isRead ? READ_GENERIC[code] : undefined) ??
        (code === 'dependency_unavailable' ? (canOpenDiagnostics() ? DEPENDENCY_ADMIN : DEPENDENCY_MEMBER) : undefined) ??
        (code === 'network_error' ? couldNotReachBaihe() : undefined) ??
        GENERIC[code] ??
        (isRead ? READ_GENERIC.application_error : GENERIC.application_error))
  const showServer =
    SERVER_TEXT_CODES.includes(code) || (opts.serverText && OPT_IN_SERVER_TEXT_CODES.includes(code))
  const rawDetail = e?.message && showServer && !ollamaText && !addressText && !engineText && !missingKey && !refusalText && !serverTitle ? safeDetail(e.message) : null
  // The server's sentence may itself send the reader to Diagnostics, which a member cannot open.
  const detail = rawDetail && (canOpenDiagnostics() || !/Diagnostics/.test(rawDetail)) ? capFirst(rawDetail) : null
  return { title, detail }
}

type EngineFailureKind = 'not_running' | 'no_model' | 'key_rejected' | 'rate_limited' | 'timed_out' | 'unreachable' | 'other'

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
          "Ollama isn't running. Install it from ollama.com (Baihe doesn't), start it, pull a model, then test again.",
      }
    }
    return local
      ? { kind: 'not_running', summary: `${engineLabel} isn't running. Start it, then test again.` }
      : { kind: 'unreachable', summary: `Couldn't reach ${engineLabel}. Check your internet connection, then test again.` }
  }
  return { kind: 'other', summary: `The ${engineLabel} test failed.` }
}
