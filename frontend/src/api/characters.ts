// Characters extras (inventory C02, C08). The base characters calls
// (list, save, clone engines, voice bank) live in ./translateStage.
import type { RememberResult, VoiceSuggestion, VoiceSuggestionResult } from '../types/characters'
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
