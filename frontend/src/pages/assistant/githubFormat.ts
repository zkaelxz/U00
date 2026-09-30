// Pure helpers for GitHub delivery (kept out of the .tsx for tests).
import { ApiError } from '../../api/client'
import { describeError, safeDetail } from '../../components/errorMessages'
import type { GithubStatus } from '../../types/assistant'
import { PC_ONLY_TEXT } from './assistantFormat'

export const TOKEN_WRITES_REFUSED =
  'The token can only be set on the Baihe PC itself, with key writes on (start.bat turns them on; otherwise set BAIHE_API_ALLOW_KEY_WRITES=1).'

/** Ready to deliver: on, with a token and a repo. */
export function githubReady(s: GithubStatus | null): boolean {
  return !!s && s.enabled && s.token_configured && !!s.repo
}

/** Why "Deliver as GitHub PR" isn't offered, or null when it is. */
export function githubNotReadyReason(s: GithubStatus | null): string | null {
  if (!s) return null
  if (!s.enabled) return 'GitHub delivery is off.'
  if (!s.token_configured) return 'No GitHub token is set.'
  if (!s.repo) return 'No repository is set.'
  return null
}

/** A title for the PR from the question that led to the fix. */
export function defaultPrTitle(question: string): string {
  const q = question.replace(/\s+/g, ' ').trim()
  const base = q ? `Fix: ${q}` : 'Fix proposed by the maintenance assistant'
  return base.length > 120 ? `${base.slice(0, 117)}…` : base
}

export function githubErrorText(e: unknown, tokenWrite = false): string {
  const err = e as Partial<ApiError> | null
  if (err?.status === 403) return tokenWrite ? TOKEN_WRITES_REFUSED : PC_ONLY_TEXT
  if (err?.status === 409 || err?.status === 422 || err?.status === 503 || err?.status === 500) {
    const detail = err?.message ? safeDetail(err.message) : null
    if (detail) return detail
  }
  const { title, detail } = describeError(e, { serverText: true })
  return detail ? `${title} ${detail}` : title
}

export const CHANGE_LABEL = { add: 'new', modify: 'changed', delete: 'deleted' } as const
