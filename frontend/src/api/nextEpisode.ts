import type { NextEpisode } from '../types/nextEpisode'
import { getJson, postJson, type Fetch } from './client'

export const getNextEpisode = (dramaId: number, f?: Fetch) => getJson<NextEpisode>(`/api/dramas/${dramaId}/next`, f)
// Puts the next title's Transcribe, Translate and Export on the queue as the caller.
export const prepareNextEpisode = (dramaId: number, f?: Fetch) =>
  postJson<NextEpisode>(`/api/dramas/${dramaId}/next/prepare`, undefined, f)
