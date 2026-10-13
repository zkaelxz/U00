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

type CardInfo = { tab: LibraryToolsTab; title: string }

// Cards are listed in the order they show; each tab starts with only its first card open.
export const CARDS = {
  series: { tab: 'organize', title: 'Series' },
  presets: { tab: 'organize', title: 'Presets' },
  'voice-bank': { tab: 'organize', title: 'Voice bank' },
  'saved-manga': { tab: 'organize', title: 'Saved manga' },
  'link-check': { tab: 'organize', title: 'Check old titles from links' },
  cost: { tab: 'activity', title: 'Cost by title' },
  history: { tab: 'activity', title: 'Reading history' },
  backup: { tab: 'backup', title: 'Back up' },
  restore: { tab: 'backup', title: 'Restore' },
  'auto-copies': { tab: 'backup', title: 'Automatic backup copies' },
  cleanup: { tab: 'storage', title: 'Cleanup' },
  'unused-clips': { tab: 'storage', title: 'Unused voice clips' },
  trash: { tab: 'storage', title: 'Trash' },
  temp: { tab: 'storage', title: 'Temp files' },
  'previous-library': { tab: 'storage', title: 'Previous library' },
  'data-folder': { tab: 'data', title: 'Folders and files' },
} as const satisfies Record<string, CardInfo>

export type LibraryToolsCard = keyof typeof CARDS

export const cardsOf = (tab: LibraryToolsTab): LibraryToolsCard[] =>
  (Object.keys(CARDS) as LibraryToolsCard[]).filter((c) => CARDS[c].tab === tab)

export function cardStartsOpen(card: LibraryToolsCard): boolean {
  return cardsOf(CARDS[card].tab)[0] === card
}

/** Where the cards' open/closed state is remembered (the title's slug, as before the tabs). */
export const cardStorageKey = (card: LibraryToolsCard) => `library.tools.${CARDS[card].title.toLowerCase().replace(/\W+/g, '-')}`
