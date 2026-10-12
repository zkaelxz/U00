// The Sources page tabs ("#/sources?tab=new"). Find has no query.
export type SourcesTab = 'find' | 'new' | 'manage'

export const SOURCES_TABS: { id: SourcesTab; label: string }[] = [
  { id: 'find', label: 'Find' },
  { id: 'new', label: 'New chapters' },
  { id: 'manage', label: 'Manage sources' },
]

export const isSourcesTab = (v: unknown): v is Exclude<SourcesTab, 'find'> => v === 'new' || v === 'manage'

export const sourcesHref = (tab?: SourcesTab) => (tab && tab !== 'find' ? `#/sources?tab=${tab}` : '#/sources')

// The areas of the Manage sources tab. They are one list of folds, not tabs; the old link
// "#/sources/manage?tab=network" scrolls to the area it names.
export type ManageTab = 'sites' | 'timing' | 'network' | 'activity'

export const MANAGE_TABS: { id: ManageTab; label: string }[] = [
  { id: 'sites', label: 'Sites' },
  { id: 'timing', label: 'Timing & cache' },
  { id: 'network', label: 'Network' },
  { id: 'activity', label: 'Activity' },
]

export const DEFAULT_MANAGE_TAB: ManageTab = 'sites'

export const isManageTab = (v: unknown): v is ManageTab => MANAGE_TABS.some((t) => t.id === v)

// In display order, areas top to bottom. Only the very first card starts open; a card that is only a button row is not listed.
export const MANAGE_CARDS = {
  sites: ['sites'],
  timing: ['pacing', 'saved-pages', 'developer'],
  network: ['proxy', 'addresses', 'layouts'],
  activity: ['imports'],
} as const satisfies Record<ManageTab, readonly string[]>

export type ManageCard = (typeof MANAGE_CARDS)[ManageTab][number]

export function cardStartsOpen(tab: ManageTab, card: ManageCard): boolean {
  return tab === MANAGE_TABS[0].id && (MANAGE_CARDS[tab] as readonly string[])[0] === card
}

export const cardStorageKey = (tab: ManageTab, card: ManageCard) => `sources.manage.${tab}.${card}`

/** The area named by a link's `?tab=`; anything else is Sites. */
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

/** The DOM id of an area's block on the Manage sources tab, which the old `?tab=` link scrolls to. */
export const manageAreaId = (area: ManageTab) => `sources-manage-${area}`
