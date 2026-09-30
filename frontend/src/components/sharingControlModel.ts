// Pure helpers for the Library's per-item sharing control (SharingControl.tsx).
import type { SessionState } from '../hooks/useSession'
import { sharingErrorText } from '../pages/settings/sharing'
import type { SharingKind } from '../types/sharing'

export interface SharingSource {
  kind: SharingKind
  is_private?: boolean | null
  owned_by_me?: boolean | null
  // Dramas only: a drama in a series follows the series.
  series_id?: number | null
}

export type SharingView =
  | { show: false }
  | { show: true; label: 'Private' | 'Shared'; follows: boolean; actionLabel: string }

/** Whether the viewer is signed in with sign-in on (the only time items have owners). */
export function signedInWithAuth(s: SessionState): boolean {
  return s.status === 'ready' && s.me.auth_enabled && s.me.signed_in
}

/** What to show for one item: nothing unless it is the viewer's own; a drama in a series only explains. */
export function sharingView(s: SessionState, item: SharingSource): SharingView {
  if (!signedInWithAuth(s) || item.owned_by_me !== true || typeof item.is_private !== 'boolean') return { show: false }
  const follows = item.kind === 'drama' && item.series_id != null
  return {
    show: true,
    label: item.is_private ? 'Private' : 'Shared',
    follows,
    actionLabel: item.is_private ? 'Share with household' : 'Make private',
  }
}

export type FlipOutcome = { isPrivate: boolean } | { error: string }

/** Ask the server to flip the item; a refusal comes back as the server's own words. */
export async function flipSharing(
  set: (kind: SharingKind, id: number, isPrivate: boolean) => Promise<{ is_private: boolean }>,
  kind: SharingKind,
  id: number,
  currentlyPrivate: boolean,
): Promise<FlipOutcome> {
  try {
    return { isPrivate: (await set(kind, id, !currentlyPrivate)).is_private }
  } catch (e) {
    return { error: sharingErrorText(e) }
  }
}

export const FOLLOWS_SERIES_NOTE = 'In a series: sharing is set for the whole series.'

export const badgeTitle = (label: 'Private' | 'Shared') =>
  label === 'Shared' ? 'Everyone in the household can see this' : 'Only you and admins can see this'
