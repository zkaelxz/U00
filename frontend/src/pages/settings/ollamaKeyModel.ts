// Pure helpers for Settings > Your Ollama key (OllamaKeyCard.tsx). The server
// decides everything (own key only, never echoed, jobs.start to change it);
// these only decide whether to show the card and how to word its state.
import type { SessionState } from '../../hooks/useSession'
import type { MemberKeyStatus } from '../../types/memberKey'
import { agoText } from './deviceSessionsModel'

export const OLLAMA_KEY_TITLE = 'Your Ollama key'
export const OLLAMA_KEYS_URL = 'https://ollama.com/settings/keys'
export const OLLAMA_KEY_NOTE =
  "Ollama's cloud models normally run on the main PC's Ollama account and use its free allowance. "
  + 'Save your own Ollama API key and your cloud runs use your account instead. '
  + 'Baihe keeps it encrypted on the main PC and never shows it again, not even to the owner.'
// Mirrors _MIN_KEY_LENGTH and _MAX_KEY_LENGTH in services/member_key_service.py and settings_service.py.
export const MIN_KEY_CHARS = 16
export const MAX_KEY_CHARS = 512

/** Signed in as a household member who may start jobs: the owner at the PC
 * with sign-in off has no account and uses the PC's own Ollama sign-in. */
export function showOllamaKey(s: SessionState): boolean {
  if (s.status !== 'ready') return false
  const me = s.me
  return me.auth_enabled && me.signed_in && !!me.user && !me.user.is_local_owner
    && me.permissions.includes('jobs.start')
}

export function keyProblem(value: string): string | null {
  const v = value.trim()
  if (!v) return 'Paste your key first.'
  if (/\s/.test(v)) return 'A key has no spaces in it.'
  if (v.length < MIN_KEY_CHARS) return 'That is too short to be an Ollama API key.'
  if (v.length > MAX_KEY_CHARS) return 'That is too long to be an Ollama API key.'
  return null
}

export function keyStateText(k: MemberKeyStatus, nowMs: number): string {
  if (k.unreadable) return 'Your saved key can no longer be read on this server. Paste it again.'
  if (!k.has_key) return 'No key saved. Cloud models use the main PC’s account, if you are allowed to use it.'
  const added = k.added_at ? `, saved ${agoText(k.added_at, nowMs)}` : ''
  return `Key ending in ${k.last4 ?? '????'}${added}. Your cloud runs use your Ollama account.`
}
