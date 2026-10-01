import type { ApiError } from '../api/client'

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
  const title =
    opts.pcOnly && (code === 'forbidden' || e?.status === 403)
      ? PC_ONLY_FORBIDDEN
      : (GENERIC[code] ?? GENERIC.application_error)
  const showServer =
    SERVER_TEXT_CODES.includes(code) || (opts.serverText && OPT_IN_SERVER_TEXT_CODES.includes(code))
  const detail = e?.message && showServer ? safeDetail(e.message) : null
  return { title, detail }
}
