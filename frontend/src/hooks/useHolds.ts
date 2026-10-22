import { usePcOnly } from './usePcOnly'
import { useSession } from './useSession'

/**
 * Whether the viewer holds `permission`, for hiding controls the server would refuse.
 * UX only: every route still enforces its own permission. With sign-in off the viewer is the
 * PC owner, who may do everything; an unreachable or still-loading /me renders optimistically
 * (App shows no page before /me answers, and the server refuses what the viewer lacks).
 */
export function useHolds(permission: string): boolean {
  const s = useSession()
  if (s.status !== 'ready') return true
  return !s.me.auth_enabled || s.me.permissions.includes(permission)
}

// Ollama cloud models: with engines.paid they use the PC's own Ollama account;
// with their own key saved in Settings, a member uses theirs instead.
export function useCloudModelsAllowed(): boolean {
  const s = useSession()
  const holdsPaid = useHolds('engines.paid')
  return holdsPaid || (s.status === 'ready' && !!s.me.own_ollama_key)
}

// Upload controls: at the PC always; from away only for an admin while the PC's remote-uploads
// switch is on, which is when the server includes media.upload in /me. Unlike useHolds this is
// not optimistic away from the PC: only a /me that lists the permission shows them.
export function useCanUpload(): boolean {
  const pc = usePcOnly()
  const s = useSession()
  if (pc !== 'remote') return true
  return s.status === 'ready' && s.me.auth_enabled && s.me.permissions.includes('media.upload')
}
