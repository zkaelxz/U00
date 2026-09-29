import type { ResearchBudget, ResearchChoice, ResearchField } from '../../types/research'

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
// field is use-or-skip; a matching value needs no choice.
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
  return []
}

// Nothing is pre-chosen to overwrite: conflicts start on "keep", and new
// values start on "keep" too, so applying writes only what the user picked.
export function defaultChoices(fields: ResearchField[]): Record<string, ResearchChoice> {
  const out: Record<string, ResearchChoice> = {}
  for (const f of fields) if (f.status !== 'same') out[f.field] = 'keep'
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
  return `${b.free_remaining} of ${b.free_daily_limit} free searches left today`
}

// What a lookup will cost, shown before it runs.
export function costLine(b: ResearchBudget, allowPaid: boolean): string {
  const paidSearch = b.free_remaining <= 0
  if (paidSearch && !allowPaid) return 'Free searches are used up for today.'
  if (b.free_tier_key) return 'Free (free-tier Gemini key).'
  const fee = paidSearch ? ` + $${b.paid_price_per_search_usd.toFixed(3)} search fee` : ''
  return `Paid Gemini key: a few tenths of a cent in tokens${fee}.`
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}
