/*
 * "Fill from link": what the link says beside what a field already holds.
 * A field with text is never filled: it only offers "Link says: …" with an
 * explicit "Use this"; an empty field gets a checkbox, ticked by default.
 */
import type { LinkDetailFields, MediaLinkProbe } from '../api/linkDetails'

export type LinkField = keyof LinkDetailFields

const LINK_FIELD_LABELS: Record<LinkField, string> = {
  title_zh: 'Title',
  author: 'Channel',
  release_date: 'Stream date',
  series: 'Series',
}

export interface LinkRow {
  field: LinkField
  label: string
  value: string // what the link says
  current: string
  // fill: the field is empty; differs: it has other text; same: it already says this
  mode: 'fill' | 'differs' | 'same'
}

export function linkValues(p: MediaLinkProbe): LinkDetailFields {
  return { title_zh: p.title, author: p.channel, release_date: p.release_date, series: p.series_suggestion }
}

export function linkRows(
  link: Partial<LinkDetailFields>,
  current: Partial<LinkDetailFields>,
  fields: LinkField[] = ['title_zh', 'author', 'release_date'],
): LinkRow[] {
  const rows: LinkRow[] = []
  for (const field of fields) {
    const value = (link[field] ?? '').trim()
    if (!value) continue
    const now = (current[field] ?? '').trim()
    rows.push({ field, label: LINK_FIELD_LABELS[field], value, current: now, mode: !now ? 'fill' : now === value ? 'same' : 'differs' })
  }
  return rows
}

export const defaultTicks = (rows: LinkRow[]): Set<LinkField> =>
  new Set(rows.filter((r) => r.mode === 'fill').map((r) => r.field))

/** The values to write: ticked rows whose field is still empty, nothing else. */
export function tickedValues(rows: LinkRow[], ticked: Set<LinkField>): Partial<LinkDetailFields> {
  const out: Partial<LinkDetailFields> = {}
  for (const r of rows) if (r.mode === 'fill' && ticked.has(r.field)) out[r.field] = r.value
  return out
}

/** The line under a New title created from a link with no name typed. */
export function createdFromLinkNote(how: 'link' | 'placeholder' | null | undefined): string | null {
  if (how === 'placeholder') return "The link couldn't be read, so the title is named after its website. Rename it in Details."
  if (how === 'link') return "The title is named from the link's own title."
  return null
}

/** The line shown before "Suggest English title" sends anything to a paid engine. */
export function englishTitleCost(
  engineName: string,
  est: { estimated_usd: number; cap_left_usd: number | null; fits_cap: boolean },
): string {
  const price = est.estimated_usd < 0.01 ? 'under $0.01' : `about $${est.estimated_usd.toFixed(2)}`
  const cap = est.cap_left_usd === null ? '' : est.fits_cap ? `; cap left $${est.cap_left_usd.toFixed(2)}` : '; over the monthly cap, so it will not run'
  return `Uses ${engineName}: ${price}${cap}.`
}
