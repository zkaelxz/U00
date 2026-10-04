// Seekable playback URLs (GET/HEAD /api/media/dramas/{id}/audio|video,
// HTTP Range, permission media.stream). The browser's <audio>/<video> element
// fetches these directly; nothing is buffered in JS.

// Same base as api/client.ts (relative by default; the Vite proxy forwards /api).
const BASE = (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export type MediaKind = 'audio' | 'video'

export function mediaStreamUrl(dramaId: number, kind: MediaKind): string {
  return `${BASE}/api/media/dramas/${dramaId}/${kind}`
}
