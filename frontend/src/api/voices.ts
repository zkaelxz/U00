// The Voices tab of Library tools (/api/voices). Reading is for anyone who may read the library; clearing,
// retraining and removing a sample are PC only and send confirm: true after the two-step button.
import type { VoiceChangeResult, VoiceListResponse } from '../types/voices'
import { getJson, postJson, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

export const getVoices = (f?: Fetch) => getJson<VoiceListResponse>('/api/voices', f)

const change = (seriesId: number, characterId: number, action: string, body: object, f?: Fetch) =>
  postJson<VoiceChangeResult>(`/api/voices/${seriesId}/${characterId}/${action}`, { ...body, confirm: true }, pcOnlyFetch(f))

export const clearVoice = (seriesId: number, characterId: number, f?: Fetch) =>
  change(seriesId, characterId, 'clear', {}, f)

export const retrainVoice = (seriesId: number, characterId: number, f?: Fetch) =>
  change(seriesId, characterId, 'retrain', {}, f)

export const removeVoiceSample = (seriesId: number, characterId: number, dramaId: number, speakerLabel: string, f?: Fetch) =>
  change(seriesId, characterId, 'remove-sample', { drama_id: dramaId, speaker_label: speakerLabel }, f)
