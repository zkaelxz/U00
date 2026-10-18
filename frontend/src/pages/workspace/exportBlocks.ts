// Pure model for the Export stage's blocks: which exports exist, the order each kind of title sees them in,
// and why one cannot run yet. No React.
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

// Every kind lists every block: a mixed title (a video with narration, a novel with a dub) loses nothing.
const ORDER: Record<TitleKind, ExportBlockId[]> = {
  video: [...VIDEO_FIRST, 'subtitles', 'epub'],
  audio: ['audiobook', 'dubbed_video', 'softsub_video', 'burned_video', 'subtitles', 'epub'],
  novel: ['epub', 'audiobook', 'subtitles', 'softsub_video', 'burned_video', 'dubbed_video'],
  // There is no comic-specific export; the subtitle file is what a comic's lines export as.
  comic: ['subtitles', 'epub', 'audiobook', 'softsub_video', 'burned_video', 'dubbed_video'],
  // Today's layout: the subtitle file, then the media jobs in their old order, then EPUB.
  subtitle: ['subtitles', 'audiobook', 'burned_video', 'softsub_video', 'dubbed_video', 'epub'],
}

export function exportBlockOrder(kind: TitleKind): ExportBlockId[] {
  return ORDER[kind]
}

/** The block that starts open when nothing is remembered. */
export function firstExportBlock(kind: TitleKind): ExportBlockId {
  return ORDER[kind][0]
}

/** Remembered per title kind, so a viewer who works on videos keeps their choice across titles. */
export function exportBlockStorageKey(kind: TitleKind, id: ExportBlockId): string {
  return `export.${kind}.${id}`
}

/** What the page knows; null means not loaded or not readable, which never blocks (the server's reason shows). */
export interface ExportInputs {
  hasSourceVideo: boolean | null
  hasDub: boolean | null
  hasNarration: boolean | null
  ffmpegFound: boolean | null
  novelNarration: boolean
}

export const NO_SOURCE_VIDEO = 'No source video yet. Add one in Media.'
export const NO_FFMPEG = 'ffmpeg is not installed.'
export const NO_NARRATION = 'There is no narration yet. Create it in Dub first.'
export const NO_DUB = 'There is no dub yet. Create it in Dub first.'
export const NOT_NOVEL_NARRATION = 'Only for titles in novel-narration mode.'

/** One line saying why the block cannot run, or null when it can. */
export function exportBlockReason(id: ExportBlockId, i: ExportInputs): string | null {
  const noVideo = i.hasSourceVideo === false
  const noFfmpeg = i.ffmpegFound === false
  switch (id) {
    case 'softsub_video':
    case 'burned_video':
      return noVideo ? NO_SOURCE_VIDEO : noFfmpeg ? NO_FFMPEG : null
    case 'dubbed_video':
      return noVideo ? NO_SOURCE_VIDEO : i.hasDub === false ? NO_DUB : noFfmpeg ? NO_FFMPEG : null
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
  if (i.hasSourceVideo === false) return NO_SOURCE_VIDEO
  if (media === 'dubbed' && i.hasDub === false) return NO_DUB
  return null
}
