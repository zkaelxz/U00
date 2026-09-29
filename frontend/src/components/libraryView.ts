// Pure helpers for the Library page's cards and Continue shelf (kept out of
// the component files so React fast refresh stays happy).

import { isComicType } from '../pages/comic/comicLogic'
import { routeHref } from '../router'

type Titled = { title_en: string | null; title_zh: string | null }

/** Display name: English title, else original, else "#id". */
export const dramaName = (d: Titled & { id?: number }) => d.title_en || d.title_zh || `#${d.id ?? ''}`

const CJK = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]/

// Links still name the Source stage until the workspace can land on the
// drama's current stage (ui-refresh-spec rollout task 3).
export const workspaceHref = (id: number, stage = 'source') => routeHref({ name: 'drama', id, stage })
export const readHref = (d: { id: number; media_type: string | null }) =>
  routeHref({ name: isComicType(d.media_type) ? 'comic' : 'read', id: d.id, page: null })

/** Text for a card's title tile: the first CJK character of the original
 *  title, else up to two initials of the English title, else "?". */
export function tileText(d: Titled): string {
  const cjk = (d.title_zh ?? '').match(CJK)
  if (cjk) return cjk[0]
  const words = (d.title_en || d.title_zh || '').match(/[\p{L}\p{N}]+/gu) ?? []
  const initials = words.slice(0, 2).map((w) => w[0].toUpperCase()).join('')
  return initials || '?'
}

/** One of six tile tints, stable per drama. */
export const tileHue = (id: number) => `tile-h${Math.abs(id) % 6}`

/** Up to `max` tags plus how many more there are. */
export function shownTags(tags: string[], max = 2): { shown: string[]; more: number } {
  return { shown: tags.slice(0, max), more: Math.max(0, tags.length - max) }
}

// The API stores naive UTC timestamps (datetime.utcnow().isoformat()).
export const parseTime = (iso: string | null | undefined): number => {
  if (!iso) return 0
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(t) ? 0 : t
}

export type ContinueItem =
  | { kind: 'read'; dramaId: number; title: string; titleZh: string | null; percent: number | null; at: number }
  | { kind: 'work'; dramaId: number; title: string; titleZh: string | null; status: string | null; mediaType: string | null; at: number }

interface HistoryRow extends Titled { drama_id: number; percent_complete: number | null; accessed_at: string | null }
interface RecentRow extends Titled { id: number; status: string | null; updated_at: string | null; media_type: string | null }

/** The Continue shelf: reading history and recently active dramas, newest
 *  first, one entry per drama (whichever activity is newer). */
export function continueItems(history: HistoryRow[], recent: RecentRow[]): ContinueItem[] {
  const all: ContinueItem[] = [
    ...history.map((h): ContinueItem => ({
      kind: 'read', dramaId: h.drama_id, title: dramaName({ ...h, id: h.drama_id }), titleZh: h.title_zh,
      percent: h.percent_complete, at: parseTime(h.accessed_at),
    })),
    ...recent.map((r): ContinueItem => ({
      kind: 'work', dramaId: r.id, title: dramaName(r), titleZh: r.title_zh, status: r.status,
      mediaType: r.media_type, at: parseTime(r.updated_at),
    })),
  ]
  // Stable sort: on a tie the reading entry (listed first) wins.
  all.sort((a, b) => b.at - a.at)
  const seen = new Set<number>()
  return all.filter((x) => (seen.has(x.dramaId) ? false : (seen.add(x.dramaId), true)))
}

/** "1 drama" / "3 dramas". */
export const countDramas = (n: number) => `${n} ${n === 1 ? 'drama' : 'dramas'}`
