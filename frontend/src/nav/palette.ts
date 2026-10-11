/*
 * What the Ctrl+K palette lists and how typing filters it. Pages come from the
 * same registry as the menu (`visibleNavItemsFor`), so a page the person hid or
 * may not open is never offered; the open title's stages come from the stage
 * list the workspace already has. Pure, so the matching is testable without a DOM.
 */
import { routeHref, type Route } from '../router'
import { STAGE_IDS, STAGE_LABELS } from '../pages/workspace/stages'
import { navId, visibleNavItemsFor, type NavContext } from './navItems'

export interface PaletteEntry {
  id: string
  label: string
  /** Names the group in the row, since the list is one ranked list. */
  hint: string
  href: string
  /** Extra words that find the entry without showing. */
  keywords: string
}

// Words people type for a page whose label does not contain them.
const PAGE_KEYWORDS: Record<string, string> = {
  library: 'titles dramas novels comics',
  settings: 'engines keys notifications sharing devices backup disk save folder cbz',
  'library-tools': 'saved manga cbz comics chapters series presets',
  admin: 'users people accounts',
  diagnostics: 'health logs disk',
  jobs: 'queue progress running',
  translate: 'text',
}

export function paletteEntries(ctx: NavContext, route: Route): PaletteEntry[] {
  const stages: PaletteEntry[] =
    route.name === 'drama'
      ? STAGE_IDS.map((stage) => ({
          id: `stage-${stage}`,
          label: `Go to ${STAGE_LABELS[stage]}`,
          hint: 'This title',
          href: routeHref({ name: 'drama', id: route.id, stage }),
          keywords: STAGE_LABELS[stage],
        }))
      : []
  const pages = visibleNavItemsFor(ctx).map((i) => ({
    id: `page-${navId(i)}`,
    label: i.label,
    hint: 'Page',
    href: routeHref(i.target),
    keywords: PAGE_KEYWORDS[navId(i)] ?? '',
  }))
  return [...stages, ...pages]
}

// NFKC folds full-width Latin and compatibility forms so "ＪＯＢＳ" finds Jobs; CJK text is kept as written.
const fold = (s: string): string => s.normalize('NFKC').toLowerCase()

/** Lower is better: label prefix, then a word start in the label, then anywhere in the label, then keywords only. Null when the token is absent. */
function tokenScore(entry: { label: string; keywords: string }, token: string): number | null {
  const label = fold(entry.label)
  if (label.startsWith(token)) return 0
  const at = label.indexOf(token)
  if (at > 0 && /[\s\p{P}]/u.test(label[at - 1])) return 1
  if (at >= 0) return 2
  return fold(entry.keywords).includes(token) ? 3 : null
}

/** Every space-separated token must match; CJK has no word boundary, so a token is a plain substring. Ties keep registry order. */
export function filterEntries(entries: PaletteEntry[], query: string): PaletteEntry[] {
  const tokens = fold(query).split(/\s+/).filter(Boolean)
  if (tokens.length === 0) return entries
  const scored: { entry: PaletteEntry; score: number; at: number }[] = []
  entries.forEach((entry, at) => {
    let score = 0
    for (const t of tokens) {
      const s = tokenScore(entry, t)
      if (s === null) return
      score += s
    }
    scored.push({ entry, score, at })
  })
  return scored.sort((a, b) => a.score - b.score || a.at - b.at).map((s) => s.entry)
}

interface KeyLike {
  key: string
  ctrlKey: boolean
  metaKey: boolean
  altKey: boolean
  shiftKey: boolean
  isComposing?: boolean
}

/** Ctrl+K or Cmd+K. `useShortcut` ignores Ctrl and Meta combos on purpose, so the palette listens for itself; never while an IME composes. */
export function isPaletteShortcut(e: KeyLike): boolean {
  return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && !e.isComposing && e.key.toLowerCase() === 'k'
}
