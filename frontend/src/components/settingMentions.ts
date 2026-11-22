/* Finds the settings a message names: "Settings > Label" (a card's label from the Settings index) or a bare pointer like "in Settings". */
import { SETTINGS_INDEX } from '../pages/settings/settingsIndex'

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// Longest label first, so "Spend history" is not cut short by a shorter label that starts the same way.
const LABELS = [...SETTINGS_INDEX].sort((a, b) => b.label.length - a.label.length)
const CARD_BY_LABEL = new Map(LABELS.map((e) => [e.label.toLowerCase(), e.cardId]))

// "Settings > Label", else "Settings" after a word that points at it. A possessive ("Settings' default") or a
// noun ("Settings sweep") is not a pointer, so it stays text.
const MENTION = new RegExp(
  `(?:\\b(?:in|under|see|check|from|Open) )?Settings > (${LABELS.map((e) => escape(e.label)).join('|')})|\\b(?:in|under|see|check|from|Open) (Settings)(?![\\w'])`,
  'gi',
)

export type SettingsSegment = { text: string; card?: string; link?: boolean }

/** The text cut into plain pieces and setting mentions; `card` is unset for a mention of the page itself. */
export function splitSettingMentions(text: string): SettingsSegment[] {
  const out: SettingsSegment[] = []
  let at = 0
  for (const m of text.matchAll(MENTION)) {
    const labelled = m[1] !== undefined
    // The link covers "Settings > Label" whole, or just the word "Settings".
    const start = m.index + (labelled ? m[0].indexOf('Settings') : m[0].length - m[2].length)
    const end = m.index + m[0].length
    if (start > at) out.push({ text: text.slice(at, start) })
    out.push({ text: text.slice(start, end), link: true, card: labelled ? CARD_BY_LABEL.get(m[1].toLowerCase()) : undefined })
    at = end
  }
  if (at < text.length) out.push({ text: text.slice(at) })
  return out
}

