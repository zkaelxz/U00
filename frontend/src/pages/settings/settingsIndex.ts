// Static registry behind Settings search, the tab bar and each card's starting state. Every card on the
// page belongs to exactly one tab and needs an entry (a test enforces both); notable fields get their own
// entry so "ollama" finds the card that holds them.
export type SettingsTab = 'translation' | 'costs' | 'notifications' | 'sharing' | 'integrations' | 'system'

// Each tab starts with only its first card open until the person folds things their own way, so a tab
// reads as a list of one-line summaries with one card to start from.
export type SettingsCardSpec = { id: string }

export const SETTINGS_TABS: { id: SettingsTab; label: string; cards: SettingsCardSpec[] }[] = [
  {
    id: 'translation',
    label: 'Translation and keys',
    cards: [
      { id: 'engine-routing' },
      { id: 'defaults' },
      { id: 'language-packs' },
    ],
  },
  {
    id: 'costs',
    label: 'Costs',
    cards: [
      { id: 'spending' },
      { id: 'spend-history' },
      { id: 'past-costs' },
    ],
  },
  {
    id: 'notifications',
    label: 'Notifications and backups',
    cards: [
      { id: 'notifications' },
      { id: 'auto-backup' },
      { id: 'save-folder' },
      { id: 'app-updates' },
    ],
  },
  {
    id: 'sharing',
    label: 'Account and display',
    cards: [
      { id: 'sharing' },
      { id: 'display' },
      { id: 'customize-menu' },
      { id: 'devices' },
      { id: 'extension-devices' },
      { id: 'ollama-key' },
    ],
  },
  {
    id: 'integrations',
    label: 'Integrations',
    cards: [
      { id: 'jellyfin' },
      { id: 'web-search' },
      { id: 'extension' },
    ],
  },
  {
    id: 'system',
    label: 'Performance and advanced',
    cards: [
      { id: 'performance' },
      { id: 'advanced' },
      { id: 'timing-splitting' },
      { id: 'transcription-experiments' },
      { id: 'developer-mode' },
    ],
  },
]

// Cards whose every action is a PC-only call (the settings POSTs are local_only): away from the PC
// the page leaves them out instead of showing a "Run this on the main PC." note.
export const PC_ONLY_CARDS: ReadonlySet<string> = new Set([
  'defaults', 'spending', 'spend-history', 'past-costs',
  'notifications', 'auto-backup', 'save-folder', 'app-updates',
  'jellyfin', 'web-search', 'extension',
  'performance', 'advanced', 'timing-splitting', 'transcription-experiments', 'developer-mode',
])

const TAB_OF_CARD = new Map(SETTINGS_TABS.flatMap((t) => t.cards.map((c) => [c.id, t.id] as const)))
const OPEN_BY_DEFAULT = new Set(SETTINGS_TABS.map((t) => t.cards[0].id))

export const tabOfCard = (cardId: string): SettingsTab | undefined => TAB_OF_CARD.get(cardId)
export const cardStartsOpen = (cardId: string): boolean => OPEN_BY_DEFAULT.has(cardId)

export type SettingsEntry = { cardId: string; label: string; keywords: string }

const e = (cardId: string, label: string, keywords = ''): SettingsEntry => ({ cardId, label, keywords })

export const SETTINGS_INDEX: SettingsEntry[] = [
  e('engine-routing', 'Which engine does what', 'engines keys api key test gemini openai deepl routing'),
  e('defaults', 'Translation style', 'english variant locale style note default'),
  e('language-packs', 'Language packs', 'honorifics address terms kinship set phrases glossary built-in defaults'),

  e('spending', 'Spending', 'monthly cap budget cost counter reset usd'),
  e('past-costs', 'Past costs', 'cost by title history spend'),
  e('spend-history', 'Spend history', 'monthly spend chart months'),

  e('notifications', 'Notifications', 'alerts channels push email discord telegram webhook'),
  e('notifications', 'Notify when a job finishes', 'notify me completion'),
  e('auto-backup', 'Automatic backups', 'backup copies schedule frequency'),
  e('save-folder', 'Save folder', 'comic manga downloads folder'),
  e('app-updates', 'App updates', 'version upgrade release'),

  e('sharing', 'Sharing', 'share new items household library'),
  e('display', 'Display', 'page width layout wide full text size font zoom bigger smaller density compact comfortable spacing tight'),
  e('customize-menu', 'Menu items', 'customize menu navigation hide pages order'),
  e('devices', 'Signed-in devices', 'sessions sign out'),
  e('extension-devices', 'Extension tokens per computer', 'browser extension devices token pair'),
  e('ollama-key', 'Your Ollama key', 'ollama cloud api key account quota own'),

  e('jellyfin', 'Jellyfin', 'media server missing subtitles'),
  e('web-search', 'Web search', 'search lookup'),
  e('extension', 'Browser extension', 'bridge capture'),

  e('performance', 'Jobs and GPU', 'performance gpu use gpu limit jobs at once parallel free local-model memory unload ollama graphics'),
  e('performance', 'Resume interrupted batches', 'batch resume bulk auto resume startup translation batches'),
  e('performance', 'Keep free memory', 'vram ram graphics reserve other programs'),
  e('advanced', 'Offline, paths and limits', 'advanced ocr offline models downloads uploads server addresses'),
  e('advanced', 'Ollama URL', 'ollama server address endpoint local model'),
  e('advanced', 'Ollama context window', 'num_ctx tokens offline'),
  e('advanced', 'Offline Whisper model folder', 'faster-whisper hugging face'),
  e('advanced', 'OCR backend', 'tesseract paddleocr manga_ocr'),
  e('advanced', 'Downloads', 'cookies browser yt-dlp lncrawl novel downloader'),
  e('advanced', 'Program locations', 'paths cookie file tesseract lncrawl command'),
  e('advanced', 'Upload size limit', 'uploads max mb'),
  e('timing-splitting', 'Timing and splitting', 'voice detection vad silence line length split long lines sentence grouping scene break pause threshold seconds characters reset default'),
  e('transcription-experiments', 'Advanced transcription', 'transcription experiments asr whisper experimental qwen batch size mixed languages forced aligner'),
  e('developer-mode', 'Developer Mode', 'assistant maintenance ai developer'),
]

export type SettingsMatch = {
  /** Card ids with at least one matching entry. */
  cardIds: Set<string>
  /** Matching cards per tab. */
  counts: Record<SettingsTab, number>
  total: number
}

/** Entries match when every word of the query is in the label or keywords; an empty query matches nothing here. */
export function filterSettings(query: string, available: ReadonlySet<string>): SettingsMatch {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  const cardIds = new Set<string>()
  const counts = Object.fromEntries(SETTINGS_TABS.map((t) => [t.id, 0])) as Record<SettingsTab, number>
  for (const entry of SETTINGS_INDEX) {
    if (!available.has(entry.cardId) || cardIds.has(entry.cardId)) continue
    const hay = `${entry.label} ${entry.keywords}`.toLowerCase()
    if (words.length && words.every((w) => hay.includes(w))) {
      cardIds.add(entry.cardId)
      counts[tabOfCard(entry.cardId)!] += 1
    }
  }
  return { cardIds, counts, total: cardIds.size }
}

export const settingsCardKey = (cardId: string) => `settings.card.${cardId}`

export const tabFoldKeys = (tab: SettingsTab): string[] =>
  SETTINGS_TABS.find((t) => t.id === tab)!.cards.map((c) => settingsCardKey(c.id))
