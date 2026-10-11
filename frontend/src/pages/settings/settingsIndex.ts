// Static registry behind Settings search, the tab bar and each card's starting state. Every card on the
// page belongs to exactly one tab and needs an entry (a test enforces both); notable fields get their own
// entry so "ollama" finds the card that holds them.
export type SettingsTab = 'translation' | 'costs' | 'notifications' | 'sharing' | 'integrations' | 'system'

// `open`: whether the card starts open when the person has not chosen yet. A tab of up to three short
// cards starts them all open; a longer tab opens only the cards used most, so the rest read as a list
// of one-line summaries.
export type SettingsCardSpec = { id: string; open: boolean }

export const SETTINGS_TABS: { id: SettingsTab; label: string; cards: SettingsCardSpec[] }[] = [
  {
    id: 'translation',
    label: 'Translation and keys',
    cards: [
      { id: 'engine-routing', open: true },
      { id: 'defaults', open: true },
      { id: 'language-packs', open: true },
    ],
  },
  {
    id: 'costs',
    label: 'Costs',
    cards: [
      { id: 'spending', open: true },
      { id: 'spend-history', open: true },
      { id: 'past-costs', open: false },
    ],
  },
  {
    id: 'notifications',
    label: 'Notifications and backups',
    cards: [
      { id: 'notifications', open: true },
      { id: 'auto-backup', open: false },
      { id: 'save-folder', open: false },
      { id: 'app-updates', open: false },
    ],
  },
  {
    id: 'sharing',
    label: 'Sharing and devices',
    cards: [
      { id: 'sharing', open: true },
      { id: 'customize-menu', open: false },
      { id: 'devices', open: false },
      { id: 'extension-devices', open: false },
    ],
  },
  {
    id: 'integrations',
    label: 'Integrations',
    cards: [
      { id: 'jellyfin', open: true },
      { id: 'web-search', open: true },
      { id: 'extension', open: true },
    ],
  },
  {
    id: 'system',
    label: 'System',
    cards: [
      { id: 'performance', open: true },
      { id: 'advanced', open: false },
      { id: 'timing-splitting', open: false },
      { id: 'transcription-experiments', open: false },
      { id: 'developer-mode', open: false },
    ],
  },
]

const TAB_OF_CARD = new Map(SETTINGS_TABS.flatMap((t) => t.cards.map((c) => [c.id, t.id] as const)))
const OPEN_BY_DEFAULT = new Map(SETTINGS_TABS.flatMap((t) => t.cards.map((c) => [c.id, c.open] as const)))

export const tabOfCard = (cardId: string): SettingsTab | undefined => TAB_OF_CARD.get(cardId)
export const cardStartsOpen = (cardId: string): boolean => OPEN_BY_DEFAULT.get(cardId) ?? false

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
  e('customize-menu', 'Menu items', 'customize menu navigation hide pages order'),
  e('devices', 'Signed-in devices', 'sessions sign out'),
  e('extension-devices', 'Extension tokens per computer', 'browser extension devices token pair'),

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
