// Pure model for the Export stage's blocks: which exports exist, the order each kind of title sees them in,
// and why one cannot run yet. No React.
import { stageLabel } from './stages'
import type { TitleKind } from './titleKind'

export type ExportBlockId = 'softsub_video' | 'burned_video' | 'dubbed_video' | 'audiobook' | 'epub' | 'subtitles'

export const EXPORT_BLOCK_TITLES: Record<ExportBlockId, string> = {
  softsub_video: 'Video with a subtitle track',
  burned_video: 'Burned-in video',
  dubbed_video: 'Video with the dub audio',
  audiobook: 'Audiobook',
  epub: 'EPUB',
  subtitles: 'Subtitle file',
}

const VIDEO_FIRST: ExportBlockId[] = ['softsub_video', 'burned_video', 'dubbed_video', 'audiobook']

const ORDER: Record<TitleKind, ExportBlockId[]> = {
  video: [...VIDEO_FIRST, 'subtitles', 'epub'],
  audio: ['audiobook', 'dubbed_video', 'softsub_video', 'burned_video', 'subtitles', 'epub'],
  novel: ['epub', 'audiobook', 'subtitles', 'softsub_video', 'burned_video', 'dubbed_video'],
  comic: [],
  // Today's layout: the subtitle file, then the media jobs in their old order, then EPUB.
  subtitle: ['subtitles', 'audiobook', 'burned_video', 'softsub_video', 'dubbed_video', 'epub'],
}

// The exports that fit each kind. The others stay hidden rather than showing a "not available" note.
const FITS: Record<TitleKind, ExportBlockId[]> = {
  video: ['softsub_video', 'burned_video', 'dubbed_video', 'subtitles'],
  audio: ['audiobook', 'subtitles'],
  novel: ['epub', 'audiobook', 'subtitles'],
  // A comic exports its pages from Scanlate, so the Export stage has no block for it.
  comic: [],
  subtitle: ['subtitles'],
}

export type ExportFacts = Partial<Pick<ExportInputs, 'hasSourceVideo' | 'hasDub' | 'hasNarration' | 'novelNarration'>>

// A title can carry media its kind doesn't expect (a narrated video, an audio drama with a picture), so an export
// that doesn't fit the kind still shows once the title has what it works from. EPUB needs novel-narration mode,
// which only a novel has, so it is never an extra.
function hasMediaFor(id: ExportBlockId, f: ExportFacts): boolean {
  switch (id) {
    case 'softsub_video':
    case 'burned_video':
      return f.hasSourceVideo === true
    case 'dubbed_video':
      return f.hasSourceVideo === true || f.hasDub === true
    case 'audiobook':
      return f.hasNarration === true
    default:
      return false
  }
}

// The Simple view offers the two exports most titles need; the rest (burned-in, dub, audiobook, EPUB) are in Full.
export const SIMPLE_EXPORT_BLOCKS: ExportBlockId[] = ['softsub_video', 'subtitles']

/** Without `facts`, only what fits the kind; a novel's EPUB also needs novel-narration mode. */
export function exportBlockOrder(kind: TitleKind, facts: ExportFacts = {}): ExportBlockId[] {
  if (kind === 'comic') return []
  return ORDER[kind].filter((id) => {
    if (id === 'epub') return kind === 'novel' && facts.novelNarration !== false
    return FITS[kind].includes(id) || hasMediaFor(id, facts)
  })
}

export function shownExportBlocks(kind: TitleKind, simple: boolean, facts: ExportFacts = {}): ExportBlockId[] {
  return exportBlockOrder(kind, facts).filter((id) => !simple || SIMPLE_EXPORT_BLOCKS.includes(id))
}

/**
 * The block that starts open when nothing is remembered: the first shown one that can run, so a title is
 * never greeted by an export it cannot make; when none can, the first shown one.
 */
export function openingExportBlock(shown: ExportBlockId[], reason: (id: ExportBlockId) => string | null): ExportBlockId {
  return shown.find((id) => reason(id) === null) ?? shown[0]
}

/** Remembered per title kind, so a viewer who works on videos keeps their choice across titles. */
export function exportBlockStorageKey(kind: TitleKind, id: ExportBlockId): string {
  return `export.${kind}.${id}`
}

/** What the page knows; null means not loaded or not readable, which never blocks (the server's reason shows). */
export interface ExportInputs {
  // Names the first stage in the reasons, which is called differently per kind.
  kind: TitleKind
  hasSourceVideo: boolean | null
  hasDub: boolean | null
  hasNarration: boolean | null
  ffmpegFound: boolean | null
  novelNarration: boolean
}

export const noSourceVideo = (kind: TitleKind) => `No source video yet. Add one in ${stageLabel('source', kind)}.`
export const NO_FFMPEG = 'ffmpeg is not installed.'
export const NO_NARRATION = 'There is no narration yet. Create it with Listen in the Reader first.'
export const NO_DUB = 'There is no dub track for this title.'
export const NOT_NOVEL_NARRATION = 'Only for titles in novel-narration mode.'

/** One line saying why the block cannot run, or null when it can. */
export function exportBlockReason(id: ExportBlockId, i: ExportInputs): string | null {
  const noVideo = i.hasSourceVideo === false
  const noFfmpeg = i.ffmpegFound === false
  switch (id) {
    case 'softsub_video':
    case 'burned_video':
      return noVideo ? noSourceVideo(i.kind) : noFfmpeg ? NO_FFMPEG : null
    case 'dubbed_video':
      return noVideo ? noSourceVideo(i.kind) : i.hasDub === false ? NO_DUB : noFfmpeg ? NO_FFMPEG : null
    case 'audiobook':
      return i.hasNarration === false ? NO_NARRATION : noFfmpeg ? NO_FFMPEG : null
    case 'epub':
      return i.novelNarration ? null : NOT_NOVEL_NARRATION
    case 'subtitles':
      return null
  }
}

/** Why "Send to Jellyfin" cannot copy the chosen video along, or null. A dubbed video only exists once a dub does. */
export function jellyfinMediaReason(media: 'none' | 'source' | 'dubbed', i: ExportInputs): string | null {
  if (media === 'none') return null
  if (i.hasSourceVideo === false) return noSourceVideo(i.kind)
  if (media === 'dubbed' && i.hasDub === false) return NO_DUB
  return null
}
