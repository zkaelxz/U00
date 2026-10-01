// Reader-specific error copy (UX spec, Reader page "Errors"). Everything
// else goes through the shared describeError, which never shows paths or keys.

import { describeError, safeDetail } from '../../components/errorMessages'

export const BUSY_TEXT = 'The AI is busy with another request. Try again in a moment.'
export const PAID_TEXT = "This lookup uses a paid engine, which this account can't use."
export const FORBIDDEN_TEXT = 'Not allowed from this device or account.'

interface ReaderErrorText {
  title: string
  detail: string | null
  // Offer "Try again": the request was refused only because the AI was busy.
  retry: boolean
}

export function readerErrorText(err: unknown, opts: { paidEngine?: boolean } = {}): ReaderErrorText {
  const e = err as { status?: number; code?: string } | null
  if (e?.status === 429 || e?.code === 'rate_limited') return { title: BUSY_TEXT, detail: null, retry: true }
  if (e?.status === 403 || e?.code === 'forbidden') {
    return { title: opts.paidEngine ? PAID_TEXT : FORBIDDEN_TEXT, detail: null, retry: false }
  }
  const base = describeError(err)
  // The Reader service's validation messages (InvalidInputError, code
  // validation_error) are fixed text such as "No deepseek key is configured.
  // Set one in Settings first."; safeDetail still drops anything path- or key-like.
  const msg = (err as { message?: unknown } | null)?.message
  if (e?.code === 'validation_error' && typeof msg === 'string') return { ...base, detail: safeDetail(msg), retry: false }
  return { ...base, retry: false }
}
