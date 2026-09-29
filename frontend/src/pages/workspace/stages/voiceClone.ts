import type { DubConfig } from '../../../types/dub'
import type { CharacterEntry } from '../../../types/translateStage'
import type { VoiceCloneCandidates, VoiceCloneSpeakerCandidates } from '../../../types/voiceClone'

// Upload picker filter; the server checks type, size and duration itself.
export const CLIP_ACCEPT = '.wav,.mp3,.m4a,.flac,.ogg,audio/*'
export const PC_ONLY_CLIP_NOTE = 'Uploading or removing a clip is PC only.'

/** Speaker label -> clone warning from the Dub config (matched by label, never by position). */
export function cloneWarnings(cfg: DubConfig | null): Map<string, string> {
  const out = new Map<string, string>()
  for (const s of cfg?.speakers ?? []) if (s.clone_warning) out.set(s.speaker_label, s.clone_warning)
  return out
}

export function warningSummary(count: number): string | null {
  if (count === 0) return null
  return count === 1
    ? '1 speaker is not set up for cloning as chosen. See Voices and cloning.'
    : `${count} speakers are not set up for cloning as chosen. See Voices and cloning.`
}

export function candidatesFor(
  list: VoiceCloneCandidates | null,
  speakerLabel: string,
): VoiceCloneSpeakerCandidates | null {
  return list?.speakers.find((s) => s.speaker_label === speakerLabel) ?? null
}

export const formatSeconds = (s: number | null | undefined) =>
  s === null || s === undefined || !Number.isFinite(s) ? '' : `${s.toFixed(1)} s`

/** Why an extraction found no clip, in plain words (null if it found some or never ran). */
export function skipText(sp: VoiceCloneSpeakerCandidates | null): string | null {
  if (!sp || sp.candidates.length > 0 || !sp.skip_reason) return null
  if (sp.skip_reason === 'no_segments') return 'No lines or speaker turns for this speaker to cut a clip from.'
  const closest = formatSeconds(sp.closest_duration)
  if (sp.skip_reason === 'too_short') {
    return `No clip found: the closest was ${closest}, under the 3 s minimum for a clean reference.`
  }
  if (sp.skip_reason === 'too_long') {
    return `No clip found: the closest was ${closest}, over the 12 s maximum for a clean reference.`
  }
  return 'No clip found.'
}

export function clipStatus(e: CharacterEntry): string {
  if (!e.has_ref_audio) return 'No reference clip'
  return e.ref_text_present ? 'Reference clip set, with transcript' : 'Reference clip set, no transcript'
}

export function speakerTitle(e: CharacterEntry): string {
  return e.character_name && e.character_name !== e.speaker_label
    ? `${e.character_name} (${e.speaker_label})`
    : e.speaker_label
}

/** Merge a saved entry back into the list by label. */
export function replaceEntry(list: CharacterEntry[] | null, saved: CharacterEntry): CharacterEntry[] | null {
  return list && list.map((x) => (x.speaker_label === saved.speaker_label ? saved : x))
}
