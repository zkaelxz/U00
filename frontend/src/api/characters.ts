// Characters extras (inventory C02, C08). The base characters calls
// (list, save, clone engines, voice bank) live in ./translateStage.
import type { MergeResult, RememberResult, RenameResult, RenameUndo, VoiceSuggestion, VoiceSuggestionResult } from '../types/characters'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

const base = (id: number) => `/api/characters/dramas/${id}`

export const getVoiceSuggestions = (id: number, f?: Fetch) =>
  getJson<VoiceSuggestion[]>(`${base(id)}/voice-suggestions`, f)

export const answerVoiceSuggestion = (
  id: number,
  action: 'accept' | 'reject',
  s: Pick<VoiceSuggestion, 'speaker_label' | 'series_character_id'>,
  f?: Fetch,
) =>
  postJson<VoiceSuggestionResult>(
    `${base(id)}/voice-suggestions/${action}`,
    { speaker_label: s.speaker_label, series_character_id: s.series_character_id },
    f,
  )

export const rememberSeriesCharacter = (id: number, speakerLabel: string, f?: Fetch) =>
  postJson<RememberResult>(`${base(id)}/remember-series-character`, { speaker_label: speakerLabel }, f)

export const renameSpeaker = (id: number, speakerLabel: string, newName: string, f?: Fetch) =>
  postJson<RenameResult>(`${base(id)}/rename-speaker`, { speaker_label: speakerLabel, new_name: newName }, f)

export const undoRenameSpeaker = (id: number, undo: RenameUndo, f?: Fetch) =>
  postJson<RenameResult>(`${base(id)}/rename-speaker/undo`, { undo }, f)

export const mergeSpeakers = (id: number, sourceLabel: string, targetLabel: string, f?: Fetch) =>
  postJson<MergeResult>(`${base(id)}/merge-speakers`, { source_label: sourceLabel, target_label: targetLabel }, f)

export const undoMergeSpeakers = (id: number, undoId: string, f?: Fetch) =>
  postJson<MergeResult>(`${base(id)}/merge-speakers/undo`, { undo_id: undoId }, f)
