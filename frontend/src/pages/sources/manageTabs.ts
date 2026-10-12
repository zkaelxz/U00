// The Manage sources tabs ("#/sources/manage?tab=network") and which cards each holds.
// The tab is in the URL so Back returns to the tab the person came from.
export type ManageTab = 'sites' | 'timing' | 'network' | 'activity'

export const MANAGE_TABS: { id: ManageTab; label: string }[] = [
  { id: 'sites', label: 'Sites' },
  { id: 'timing', label: 'Timing & cache' },
  { id: 'network', label: 'Network' },
  { id: 'activity', label: 'Activity' },
]

export const DEFAULT_MANAGE_TAB: ManageTab = 'sites'

export const isManageTab = (v: unknown): v is ManageTab => MANAGE_TABS.some((t) => t.id === v)

// In display order. Only the first card of a tab starts open; a card that is only a button row is not listed.
export const MANAGE_CARDS = {
  sites: ['sites'],
  timing: ['pacing', 'saved-pages', 'developer'],
  network: ['proxy', 'addresses', 'layouts'],
  activity: ['imports'],
} as const satisfies Record<ManageTab, readonly string[]>

export type ManageCard = (typeof MANAGE_CARDS)[ManageTab][number]

export function cardStartsOpen(tab: ManageTab, card: ManageCard): boolean {
  return (MANAGE_CARDS[tab] as readonly string[])[0] === card
}

export const cardStorageKey = (tab: ManageTab, card: ManageCard) => `sources.manage.${tab}.${card}`

/** The tab named by a link's `?tab=`; anything else opens Sites. */
export const tabFromParam = (v: string | null): ManageTab => (isManageTab(v) ? v : DEFAULT_MANAGE_TAB)

export const manageHref = (tab?: ManageTab) => (tab && tab !== DEFAULT_MANAGE_TAB ? `#/sources/manage?tab=${tab}` : '#/sources/manage')

/** What a tab says when every section on it is empty, so it is never blank. */
export function emptyTabMessage(tab: ManageTab): string {
  switch (tab) {
    case 'sites':
      return 'No sites are available.'
    case 'timing':
    case 'network':
      return 'These settings could not be loaded. Reload the page to try again.'
    case 'activity':
      return 'Nothing yet. Pages you import from a link are listed here.'
  }
}
