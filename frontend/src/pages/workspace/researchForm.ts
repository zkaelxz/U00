import { historyTime } from '../translatePage'
import type { ProvenanceRow, ResearchBudget, ResearchChoice, ResearchField, ResearchMode } from '../../types/research'

// Pure logic for the Source stage's "Research online" panel (Step 37).

const LABELS: Record<string, string> = {
  title_en: 'English title',
  title_zh: 'Chinese title',
  author: 'Author',
  studio: 'Studio',
  director: 'Director',
  voice_actors: 'Voice actors',
  summary: 'Summary',
}

export const fieldLabel = (key: string) => LABELS[key] ?? key

// The choices offered for a row. A conflict always gets all three; an empty
// field is use-or-skip; a matching value can have its sources recorded.
export function choicesFor(f: ResearchField): { value: ResearchChoice; label: string }[] {
  if (f.status === 'conflict') {
    return [
      { value: 'keep', label: 'Keep existing' },
      { value: 'replace', label: 'Replace' },
      { value: 'save_both', label: 'Save both' },
    ]
  }
  if (f.status === 'new') {
    return [
      { value: 'keep', label: 'Skip' },
      { value: 'replace', label: 'Use' },
    ]
  }
  return [
    { value: 'keep', label: 'Leave' },
    { value: 'confirm', label: 'Record sources' },
  ]
}

// Nothing is pre-chosen to overwrite: conflicts start on "keep", and new
// values start on "keep" too, so applying writes only what the user picked.
export function defaultChoices(fields: ResearchField[]): Record<string, ResearchChoice> {
  const out: Record<string, ResearchChoice> = {}
  for (const f of fields) out[f.field] = 'keep'
  return out
}

// The drama's value the user was shown next to each chosen field.
export function seenValues(fields: ResearchField[], choices: Record<string, ResearchChoice>): Record<string, string | null> {
  const out: Record<string, string | null> = {}
  for (const f of fields) if (choices[f.field] && choices[f.field] !== 'keep') out[f.field] = f.current ?? null
  return out
}

// Only the choices that write something (a "keep" is dropped).
export function effectiveChoices(choices: Record<string, ResearchChoice>): Record<string, ResearchChoice> {
  return Object.fromEntries(Object.entries(choices).filter(([, c]) => c !== 'keep'))
}

export function confidenceLabel(c: number | null | undefined): string {
  if (c == null) return 'confidence unknown'
  if (c >= 0.8) return 'high confidence'
  if (c >= 0.5) return 'medium confidence'
  return 'low confidence'
}

export function budgetLine(b: ResearchBudget): string {
  return `${b.free_remaining} free searches left today`
}

export const isPaidLookup = (b: ResearchBudget) => b.free_remaining < (b.free_lookup_min || 1)

export const usd = (n: number) => (n > 0 && n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`)

// What a lookup will cost, shown before it runs.
export function costLine(b: ResearchBudget, mode: ResearchMode, model: string, allowPaid: boolean): string {
  const paidSearch = isPaidLookup(b)
  if (paidSearch && !allowPaid) return 'The free searches are used up for now.'
  if (b.free_tier_key) return 'Free (free-tier Gemini key).'
  const tokens = b.estimates_usd?.[mode]?.[model] ?? 0
  if (!paidSearch) return `About ${usd(tokens)} on your paid Gemini key.`
  // One lookup may run several searches, each billed once the free ones are used.
  const fee = b.paid_price_per_search_usd * (b.free_lookup_min || 1)
  return `Up to ${usd(tokens + fee)} on your paid Gemini key (includes the search fees).`
}

// Google's grounding terms ask for the search suggestions to be shown.
export const googleSearchUrl = (q: string) => `https://www.google.com/search?q=${encodeURIComponent(q)}`

export interface ProvenanceNote { field: string; label: string; text: string }

// One note per field, from its newest stored row. Only source titles are
// named (never URLs), and the date is when the sources were checked.
export function provenanceNotes(rows: ProvenanceRow[]): ProvenanceNote[] {
  const newest = new Map<string, ProvenanceRow>()
  for (const r of rows) {
    const seen = newest.get(r.field)
    if (!seen || r.id > seen.id) newest.set(r.field, r)
  }
  return [...newest.values()].sort((a, b) => a.id - b.id).map((r) => {
    const titles = [...new Set(r.sources.map((s) => (s.title ?? '').trim()).filter(Boolean))].slice(0, 3)
    const from = titles.length ? ` from ${titles.join(', ')}` : ''
    const when = r.last_verified || r.retrieved_at
    const on = when ? ` on ${historyTime(when)}` : ''
    const verb = r.status === 'alternate' ? 'Saved beside the existing value' : r.status === 'verified' ? 'Confirmed' : 'Filled'
    return { field: r.field, label: fieldLabel(r.field), text: `${verb}${from}${on}` }
  })
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}
