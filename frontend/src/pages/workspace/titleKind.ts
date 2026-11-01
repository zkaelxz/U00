import { mediaKind } from './detailsForm'

/**
 * What a title mostly is, for stages that lead with the part that matters for it. Builds on `mediaKind`
 * (novel / comic / the audio default) and splits the rest by whether the media type carries a picture.
 * 'subtitle' is a title with no media type that implies video or audio: it keeps the plain layout.
 */
export type TitleKind = 'video' | 'audio' | 'novel' | 'comic' | 'subtitle'

const VIDEO_TYPES = new Set(['video_drama', 'anime', 'streamer_vod'])
const AUDIO_TYPES = new Set(['audio_drama', 'asmr'])

export function titleKind(mediaType: string | null | undefined, contentMode?: string | null): TitleKind {
  // Narrated novels are worked from novel text whatever the media type says (same rule as novelSections).
  if (contentMode === 'novel_narration') return 'novel'
  const base = mediaKind(mediaType)
  if (base !== 'audio') return base
  if (mediaType && VIDEO_TYPES.has(mediaType)) return 'video'
  if (mediaType && AUDIO_TYPES.has(mediaType)) return 'audio'
  return 'subtitle'
}

export const TITLE_KIND_LABEL: Record<TitleKind, string> = {
  video: 'Video title',
  audio: 'Audio title',
  novel: 'Novel',
  comic: 'Comic',
  subtitle: 'Subtitle-only title',
}

/**
 * Novel and comic titles get the short Translate page: one primary button, with the engine, style and run
 * options inside the existing fold. Video, audio and subtitle-only titles keep every control in view because
 * they are tuned per run far more often.
 */
export const foldsTranslateOptions = (kind: TitleKind): boolean => kind === 'novel' || kind === 'comic'
