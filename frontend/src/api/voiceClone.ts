import type { CharacterEntry, VoiceBankEntry } from '../types/translateStage'
import type { VoiceCloneCandidates } from '../types/voiceClone'
import { apiUrl, getJson, postJson, postMultipart } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

// Voice-clone setup (api/routers/voice_clone_routes.py). Speaker labels go in
// the body (they may contain spaces or slashes), never in the path.
const base = (dramaId: number) => `/api/characters/dramas/${dramaId}`

// PC-only: upload / replace and remove a reference clip.
export function uploadReferenceClip(
  dramaId: number,
  speakerLabel: string,
  file: Blob,
  fileName: string,
  refText?: string,
  f?: Fetch,
) {
  const form = new FormData()
  form.append('speaker_label', speakerLabel)
  if (refText !== undefined && refText.trim()) form.append('ref_text', refText.trim())
  form.append('file', file, fileName)
  return postMultipart<CharacterEntry>(`${base(dramaId)}/reference-clip`, form, pcOnlyFetch(f))
}

export const removeReferenceClip = (dramaId: number, speakerLabel: string, f?: Fetch) =>
  postJson<CharacterEntry>(
    `${base(dramaId)}/reference-clip/remove`,
    { speaker_label: speakerLabel, confirm: true },
    pcOnlyFetch(f),
  )

export const extractCandidates = (dramaId: number, speakerLabel: string, maxCandidates = 3, f?: Fetch) =>
  postJson<{ job_id: string }>(
    `${base(dramaId)}/reference-clips/extract`,
    { speaker_label: speakerLabel, max_candidates: maxCandidates },
    f,
  )

export const listCandidates = (dramaId: number, f?: Fetch) =>
  getJson<VoiceCloneCandidates>(`${base(dramaId)}/reference-clips/candidates`, f)

// A plain <audio src>; the browser streams it.
export const candidateAudioUrl = (dramaId: number, candidateId: string) =>
  apiUrl(`${base(dramaId)}/reference-clips/candidates/${encodeURIComponent(candidateId)}/audio`)

export const chooseCandidate = (dramaId: number, candidateId: string, f?: Fetch) =>
  postJson<CharacterEntry>(
    `${base(dramaId)}/reference-clips/candidates/${encodeURIComponent(candidateId)}/choose`,
    undefined,
    f,
  )

export const saveToVoiceBank = (dramaId: number, speakerLabel: string, name: string, notes = '', f?: Fetch) =>
  postJson<VoiceBankEntry>(
    `${base(dramaId)}/voice-bank/save`,
    { speaker_label: speakerLabel, name: name.trim(), notes },
    f,
  )

export const linkSeriesCharacter = (
  dramaId: number,
  speakerLabel: string,
  seriesCharacterId: number | null,
  f?: Fetch,
) =>
  postJson<CharacterEntry>(
    `${base(dramaId)}/series-link`,
    { speaker_label: speakerLabel, series_character_id: seriesCharacterId },
    f,
  )
