// The Library tools tabs and which tab holds each card. A deep link names a tab
// ("#/library-tools?tab=storage"); the other pages' text points at the same names.
export type LibraryToolsTab = 'organize' | 'activity' | 'backup' | 'storage' | 'data'

export const LIBRARY_TOOLS_TABS: { id: LibraryToolsTab; label: string }[] = [
  { id: 'organize', label: 'Organize' },
  { id: 'activity', label: 'Activity' },
  { id: 'backup', label: 'Backup' },
  { id: 'storage', label: 'Storage' },
  { id: 'data', label: 'Data folder' },
]

export const isLibraryToolsTab = (v: unknown): v is LibraryToolsTab => LIBRARY_TOOLS_TABS.some((t) => t.id === v)

type CardInfo = { tab: LibraryToolsTab; title: string; mostUsed?: boolean }

// `mostUsed` only matters on a tab with more than SHORT_TAB_MAX cards: those start with just
// the most used cards open, so the page is not a wall of forms.
export const CARDS = {
  series: { tab: 'organize', title: 'Series', mostUsed: true },
  presets: { tab: 'organize', title: 'Presets' },
  'voice-bank': { tab: 'organize', title: 'Voice bank' },
  'saved-manga': { tab: 'organize', title: 'Saved manga' },
  cost: { tab: 'activity', title: 'Cost by title' },
  history: { tab: 'activity', title: 'Reading history' },
  export: { tab: 'backup', title: 'Export', mostUsed: true },
  backup: { tab: 'backup', title: 'Backup', mostUsed: true },
  'user-backup': { tab: 'backup', title: 'Backup of just my stuff' },
  restore: { tab: 'backup', title: 'Restore' },
  'auto-copies': { tab: 'backup', title: 'Automatic backup copies', mostUsed: true },
  cleanup: { tab: 'storage', title: 'Cleanup', mostUsed: true },
  'unused-clips': { tab: 'storage', title: 'Unused voice clips' },
  trash: { tab: 'storage', title: 'Trash', mostUsed: true },
  temp: { tab: 'storage', title: 'Temp files' },
  'previous-library': { tab: 'storage', title: 'Previous library', mostUsed: true },
  'data-folder': { tab: 'data', title: 'Folders and files', mostUsed: true },
} as const satisfies Record<string, CardInfo>

export type LibraryToolsCard = keyof typeof CARDS

export const SHORT_TAB_MAX = 3

export const cardsOf = (tab: LibraryToolsTab): LibraryToolsCard[] =>
  (Object.keys(CARDS) as LibraryToolsCard[]).filter((c) => CARDS[c].tab === tab)

export function cardStartsOpen(card: LibraryToolsCard): boolean {
  const info: CardInfo = CARDS[card]
  return cardsOf(info.tab).length <= SHORT_TAB_MAX || !!info.mostUsed
}

/** Where the cards' open/closed state is remembered (the title's slug, as before the tabs). */
export const cardStorageKey = (card: LibraryToolsCard) => `library.tools.${CARDS[card].title.toLowerCase().replace(/\W+/g, '-')}`
