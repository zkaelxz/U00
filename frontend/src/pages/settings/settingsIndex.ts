// Static registry behind Settings search and the tab counts. Every card on the page needs an entry
// (a test enforces it); notable fields get their own entry so "ollama" finds the card that holds them.
export type SettingsTab = 'translation' | 'preferences' | 'system'

export const SETTINGS_TABS: { id: SettingsTab; label: string }[] = [
  { id: 'translation', label: 'Translation and keys' },
  { id: 'preferences', label: 'Preferences' },
  { id: 'system', label: 'System' },
]

export type SettingsEntry = { tab: SettingsTab; cardId: string; label: string; keywords: string }

const e = (tab: SettingsTab, cardId: string, label: string, keywords = ''): SettingsEntry => ({ tab, cardId, label, keywords })

export const SETTINGS_INDEX: SettingsEntry[] = [
  e('translation', 'engine-routing', 'Which engine does what', 'engines keys api key test gemini openai deepl routing'),
  e('translation', 'defaults', 'Translation style', 'english variant locale style note default'),
  e('translation', 'spending', 'Spending', 'monthly cap budget cost counter reset usd'),
  e('translation', 'past-costs', 'Past costs', 'cost by drama history spend'),
  e('translation', 'spend-history', 'Spend history', 'monthly spend chart months'),

  e('preferences', 'notify-toggle', 'Notify when a job finishes', 'notifications completion'),
  e('preferences', 'notifications', 'Notifications', 'alerts channels push email discord telegram webhook'),
  e('preferences', 'auto-backup', 'Automatic backups', 'backup copies schedule frequency'),
  e('preferences', 'save-folder', 'Save folder', 'comic manga downloads folder'),
  e('preferences', 'app-updates', 'App updates', 'version upgrade release'),
  e('preferences', 'sharing', 'Sharing', 'share new items household library'),
  e('preferences', 'customize-menu', 'Customize menu', 'navigation hide pages order'),
  e('preferences', 'devices', 'Signed-in devices', 'sessions sign out'),
  e('preferences', 'extension-devices', 'Extension devices', 'browser extension token pair'),
  e('preferences', 'jellyfin', 'Jellyfin', 'media server missing subtitles'),
  e('preferences', 'web-search', 'Web search', 'search lookup'),
  e('preferences', 'extension', 'Browser extension', 'bridge capture'),

  e('system', 'performance', 'Performance', 'gpu use gpu limit jobs at once parallel unload ollama graphics'),
  e('system', 'loaded-models', 'Loaded models', 'memory vram unload ollama whisper'),
  e('system', 'auto-resume', 'Resume interrupted batches', 'bulk auto resume startup translation batches'),
  e('system', 'advanced', 'Advanced', 'ocr offline models memory to keep free downloads uploads server addresses'),
  e('system', 'advanced', 'Ollama URL', 'ollama server address endpoint local model'),
  e('system', 'advanced', 'Ollama context window', 'num_ctx tokens offline performance'),
  e('system', 'advanced', 'Offline Whisper model folder', 'faster-whisper hugging face'),
  e('system', 'advanced', 'Keep free memory', 'vram ram graphics reserve'),
  e('system', 'advanced', 'OCR backend', 'tesseract paddleocr manga_ocr'),
  e('system', 'advanced', 'Downloads', 'cookies browser yt-dlp lncrawl novel downloader'),
  e('system', 'advanced', 'Upload size limit', 'uploads max mb'),
  e('system', 'transcription-experiments', 'Transcription experiments', 'asr whisper experimental'),
  e('system', 'developer-mode', 'Developer Mode', 'assistant maintenance ai developer'),
  e('system', 'ports', 'Ports', 'diagnostics network port'),
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
  const counts: Record<SettingsTab, number> = { translation: 0, preferences: 0, system: 0 }
  for (const entry of SETTINGS_INDEX) {
    if (!available.has(entry.cardId) || cardIds.has(entry.cardId)) continue
    const hay = `${entry.label} ${entry.keywords}`.toLowerCase()
    if (words.length && words.every((w) => hay.includes(w))) {
      cardIds.add(entry.cardId)
      counts[entry.tab] += 1
    }
  }
  return { cardIds, counts, total: cardIds.size }
}
