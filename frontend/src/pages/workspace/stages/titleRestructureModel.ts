import type {
  TitleRestructureKind,
  TitleRestructurePreview,
  TitleRestructureResult,
} from '../../../types/titleRestructure'
import { plural } from '../../../labels'

/** What the confirm button does, in words. */
export function confirmLabel(p: Pick<TitleRestructurePreview, 'kind' | 'moving_chapters' | 'target_title'>): string {
  return p.kind === 'merge'
    ? `Merge into “${p.target_title}”`
    : `Move ${plural(p.moving_chapters, 'chapter')} to “${p.target_title}”`
}

export function previewSummary(p: TitleRestructurePreview): string {
  const pages = p.media === 'comic' ? ` (${plural(p.moving_pages, 'page')})` : ''
  const empty = p.kind === 'merge' && p.source_empty_after ? ' This title will be left empty.' : ''
  return `${plural(p.moving_chapters, 'chapter')}${pages} will move to the end of “${p.target_title}”.${empty}`
}

export function resultText(r: TitleRestructureResult, targetTitle: string): string {
  const pages = r.moved_pages ? ` (${plural(r.moved_pages, 'page')})` : ''
  const empty = r.source_empty ? " This title is now empty: delete it from the Library when you're sure." : ''
  return (
    `Moved ${plural(r.moved_chapters, 'chapter')}${pages} to “${targetTitle}”.${empty} ` +
    `A copy of the library database was saved first, in the library's backups/restructure folder as ${r.snapshot}. ` +
    'To undo, move the chapters back from that title.'
  )
}

// The snapshot holds the database and the label or raw-novel files, never
// page images (those are moved, not copied), so it can't promise to restore a
// comic's pages: undo is moving the chapters back.
export function safetyNote(media: TitleRestructurePreview['media']): string {
  const saved = media === 'novel'
    ? "the library database and both titles' original text"
    : "the library database and both titles' chapter labels (page images are moved, not copied)"
  return `A copy of ${saved} is saved before anything moves. Nothing is deleted; to undo, move the chapters back.`
}

/** Why Preview can't be pressed yet, or null when it can. */
export function previewBlocker(kind: TitleRestructureKind, targetId: string, picked: number): string | null {
  if (!targetId) return 'Still needed: the title to move to.'
  if (kind === 'move' && picked === 0) return 'Still needed: at least one chapter.'
  return null
}

/** How a chapter is listed: its name and size. */
export function chapterSize(c: { pages: number | null; chars: number | null }): string {
  if (c.pages != null) return plural(c.pages, 'page')
  if (c.chars != null) return `${c.chars.toLocaleString('en-US')} characters`
  return ''
}
