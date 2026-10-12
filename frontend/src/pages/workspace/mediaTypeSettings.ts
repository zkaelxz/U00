import type { TitleKind } from './titleKind'

/**
 * Which groups of Media-stage settings mean something for each kind of title. A setting whose group is
 * not listed for the title's kind is not rendered at all (not merely disabled): a novel has no audio to
 * tune and a comic no speech to detect. Every Media-stage panel asks `appliesTo`, so a new kind or group
 * is decided here once.
 */
export type SettingGroup =
  | 'transcript_source' // the "where the lines come from" picker
  | 'transcribe' // Transcribe tab: language, Whisper model, run button
  | 'speech_tuning' // Tools tab: ASR backend, VAD, min silence, beam size, auto-tune, switches
  | 'audio_prep' // separating vocals before transcribing
  | 'speakers' // speaker count hints and detection
  | 'hardsub' // reading captions burned into the picture
  | 'media_upload' // attaching an audio or video file
  | 'subtitle_import' // SRT, VTT, ASS or LRC with timings
  | 'novel_text' // pasted, EPUB or scraped text to translate
  | 'chapter_ocr' // reading text from page images

const SPEECH: readonly SettingGroup[] = ['transcript_source', 'transcribe', 'speech_tuning', 'audio_prep', 'speakers', 'media_upload', 'subtitle_import', 'novel_text']

export const SETTING_GROUPS_BY_KIND: Record<TitleKind, readonly SettingGroup[]> = {
  video: [...SPEECH, 'hardsub'],
  // No picture, so nothing to read captions from.
  audio: [...SPEECH],
  // A title with no media type that implies picture or sound keeps every control.
  subtitle: [...SPEECH, 'hardsub'],
  // Audio comes after translation (dubbing), so only the upload stays; the rest needs timed speech.
  novel: ['novel_text', 'media_upload'],
  comic: ['novel_text', 'chapter_ocr'],
}

export const appliesTo = (kind: TitleKind, group: SettingGroup): boolean => SETTING_GROUPS_BY_KIND[kind].includes(group)
