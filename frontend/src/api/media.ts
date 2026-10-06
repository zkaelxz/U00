// Seekable playback URLs (GET/HEAD /api/media/dramas/{id}/audio|video,
// HTTP Range, permission media.stream). The browser's <audio>/<video> element
// fetches these directly; nothing is buffered in JS.

// Same base as api/client.ts (relative by default; the Vite proxy forwards /api).
import { getJson } from './client'

const BASE = (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export type MediaKind = 'audio' | 'video'

export function mediaStreamUrl(dramaId: number, kind: MediaKind): string {
  return `${BASE}/api/media/dramas/${dramaId}/${kind}`
}

export interface MediaPeaks {
  start: number
  end: number
  buckets: number
  // 0-255 loudness peak per bucket.
  peaks: number[]
}

// GET /api/media/dramas/{id}/peaks: peaks of a window of the audio for the
// Review waveform (permission media.stream, like the stream itself). The
// signal lets a superseded pan or zoom drop its request.
export function getPeaks(
  dramaId: number,
  start: number,
  end: number,
  buckets: number,
  signal?: AbortSignal,
  f: typeof fetch = fetch,
): Promise<MediaPeaks> {
  return getJson<MediaPeaks>(
    `/api/media/dramas/${dramaId}/peaks?start=${start}&end=${end}&buckets=${buckets}`,
    (input, init) => f(input, { ...init, signal }),
  )
}
