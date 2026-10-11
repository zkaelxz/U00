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
