// GET /api/library/voice-bank/{id}/audio (media.stream): one voice-bank
// entry's saved clip, audio only. Used as an <audio>/Audio() source.
import { apiUrl } from './client'

export const voiceBankAudioUrl = (entryId: number) =>
  apiUrl(`/api/library/voice-bank/${encodeURIComponent(String(entryId))}/audio`)
