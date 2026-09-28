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
}

const SERVER_TEXT_CODES = ['not_found', 'conflict', 'unsupported_operation', 'dependency_unavailable']

const SENSITIVE =
  /(?:[A-Za-z]:\\|\\\\|\/(?:home|Users|tmp|var|etc|root|opt|mnt)\/|\bsk-[\w-]{6,}|\bBearer\s+\S+|(?:api[_-]?key|token|secret)\s*[=:])/i

export function safeDetail(message: string): string | null {
  const m = message.trim()
  if (!m || m.length > 200 || SENSITIVE.test(m)) return null
  return m
}

export function describeError(err: unknown): { title: string; detail: string | null } {
  const e = err as Partial<ApiError> | null
  const code = e?.code ?? 'internal_error'
  const title = GENERIC[code] ?? GENERIC.application_error
  const detail = e?.message && SERVER_TEXT_CODES.includes(code) ? safeDetail(e.message) : null
  return { title, detail }
}
