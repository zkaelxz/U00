import { readPref, writePref } from '../../../../hooks/usePersistedState'
import { sectionStorageKey, writeSectionOpen, type StorageLike } from '../../../../components/sectionStorage'

// The Review tools panel under the line list: one tab level of groups named by
// purpose. A deep link names a group ("#/drama/3/review?tools=problems").
export type ReviewToolsGroup = 'problems' | 'change' | 'records' | 'finish'

export const REVIEW_TOOLS_GROUPS: { id: ReviewToolsGroup; label: string }[] = [
  { id: 'problems', label: 'Look for problems' },
  { id: 'change', label: 'Change many lines' },
  { id: 'records', label: 'Records' },
  { id: 'finish', label: 'Finish' },
]

export type ReviewTool =
  | 'checks' | 'flags' | 'coverage' | 'compareTranscription' | 'audioTags'
  | 'fixFlagged' | 'retranslate' | 'threeRun' | 'structure' | 'resplit' | 'merge' | 'shorten' | 'cleanup' | 'retime' | 'retranscribe' | 'voices'
  | 'records' | 'versionsAndNotes' | 'style'
  | 'burnPreview' | 'markReviewed'

// Every tool once, in the order its group shows it. `simple` marks the tools a
// reviewer uses every time: Simple shows only those, and only the groups that hold one.
export const REVIEW_TOOLS: { id: ReviewTool; group: ReviewToolsGroup; simple?: true }[] = [
  { id: 'checks', group: 'problems', simple: true },
  { id: 'flags', group: 'problems', simple: true },
  { id: 'coverage', group: 'problems' },
  { id: 'compareTranscription', group: 'problems' },
  { id: 'audioTags', group: 'problems' },
  { id: 'fixFlagged', group: 'change', simple: true },
  { id: 'retranslate', group: 'change' },
  { id: 'threeRun', group: 'change' },
  { id: 'structure', group: 'change' },
  { id: 'resplit', group: 'change' },
  { id: 'merge', group: 'change' },
  { id: 'shorten', group: 'change' },
  { id: 'cleanup', group: 'change' },
  { id: 'retime', group: 'change' },
  { id: 'retranscribe', group: 'change' },
  { id: 'voices', group: 'change' },
  { id: 'records', group: 'records' },
  { id: 'versionsAndNotes', group: 'records' },
  { id: 'style', group: 'records' },
  { id: 'burnPreview', group: 'finish' },
  { id: 'markReviewed', group: 'finish', simple: true },
]

// `added`: tools a button on the page asked for (Transcribe this gap needs Re-transcribe), shown in Simple too.
export const shownTools = (simple: boolean, added: ReadonlySet<ReviewTool> = new Set()) =>
  REVIEW_TOOLS.filter((t) => !simple || t.simple || added.has(t.id))

export const shownGroups = (simple: boolean, added: ReadonlySet<ReviewTool> = new Set()) => {
  const tools = shownTools(simple, added)
  return REVIEW_TOOLS_GROUPS.filter((g) => tools.some((t) => t.group === g.id))
}

export const groupOfTool = (tool: ReviewTool): ReviewToolsGroup => REVIEW_TOOLS.find((t) => t.id === tool)!.group

export const isReviewToolsGroup = (v: unknown): v is ReviewToolsGroup => REVIEW_TOOLS_GROUPS.some((g) => g.id === v)

// The seven tabs before the tools were grouped by purpose: links, bookmarks and
// remembered choices still name them, so each opens the group that now holds
// that tab's first tool.
const OLD_TAB_GROUP: Record<string, ReviewToolsGroup> = {
  check: 'problems',
  fix: 'change',
  coverage: 'problems',
  voices: 'change',
  restructure: 'change',
  history: 'records',
  extras: 'problems',
}

export function asReviewToolsGroup(v: unknown): ReviewToolsGroup | null {
  if (isReviewToolsGroup(v)) return v
  return typeof v === 'string' && Object.hasOwn(OLD_TAB_GROUP, v) ? OLD_TAB_GROUP[v] : null
}

// A finished comparison's link from before the grouping was "?tools=history&focus=compare";
// Compare transcription is no longer with the records, so the focus decides the group.
export function reviewToolsGroupForLink(tools: string | null, focus: string | null): ReviewToolsGroup | null {
  const group = asReviewToolsGroup(tools)
  return group && focus === 'compare' ? groupOfTool('compareTranscription') : group
}

// The card that opens the first time a group is shown; the rest start closed. A
// card that only holds a button row is not a fold, so it is never listed here.
// Change many lines and Records have none: each holds enough tools that any one
// opened first would push the rest off the screen.
export const FIRST_CARD: Record<ReviewToolsGroup, string | null> = {
  problems: 'review.consistency',
  change: null,
  records: null,
  finish: 'review.aiExtras.burn',
}

const tabPrefKey = (dramaId: number) => `review.tools.tab.${dramaId}`
const addedPrefKey = (dramaId: number) => `review.tools.added.${dramaId}`

// Kept per title so a run left before Apply can still be reached in Simple on the next visit.
export function readAddedTools(storage: StorageLike | null, dramaId: number): ReadonlySet<ReviewTool> {
  const saved = readPref<unknown>(storage, addedPrefKey(dramaId), [])
  return new Set(Array.isArray(saved) ? REVIEW_TOOLS.filter((t) => saved.includes(t.id)).map((t) => t.id) : [])
}

export function writeAddedTools(storage: StorageLike | null, dramaId: number, tools: ReadonlySet<ReviewTool>): void {
  writePref(storage, addedPrefKey(dramaId), [...tools])
}
const seededPrefKey = (group: ReviewToolsGroup) => `review.tools.seeded.${group}`

export function readLastTab(storage: StorageLike | null, dramaId: number): ReviewToolsGroup {
  return asReviewToolsGroup(readPref<string>(storage, tabPrefKey(dramaId), 'problems')) ?? 'problems'
}

export function writeLastTab(storage: StorageLike | null, dramaId: number, group: ReviewToolsGroup): void {
  writePref(storage, tabPrefKey(dramaId), group)
}

// Opens the group's first card once per browser, before its sections mount, so
// the viewer's own later open/close choice is what Section remembers. A card
// the viewer already set either way is left alone.
export function seedFirstCard(storage: StorageLike | null, group: ReviewToolsGroup): void {
  const card = FIRST_CARD[group]
  if (!storage || !card || readPref(storage, seededPrefKey(group), false)) return
  try {
    if (storage.getItem(sectionStorageKey(card)) === null) writeSectionOpen(storage, card, true)
  } catch {
    return
  }
  writePref(storage, seededPrefKey(group), true)
}
