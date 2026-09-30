// Pure text helpers for the Jellyfin connector (Settings card and the Export
// stage's "Send to Jellyfin"). Unit-tested.
import { ApiError } from '../../api/client'
import type { JellyfinConfig, JellyfinScanItem, JellyfinScanReport, JellyfinSendResult } from '../../types/jellyfin'

export const KEY_WRITES_REFUSED =
  'The Jellyfin key can only be set on the Baihe PC itself, with key writes turned on (start the API with BAIHE_API_ALLOW_KEY_WRITES=1).'

export function jellyfinSummary(c: JellyfinConfig): string {
  if (!c.enabled) return 'Off'
  if (!c.server_url || !c.key_configured) return 'On, not set up'
  return c.library_dir ? 'On' : 'On, no library folder'
}

// Ready to send: switched on, with an address, a key and a library folder.
export function readyToSend(c: JellyfinConfig | null): boolean {
  return !!c && c.enabled && !!c.server_url && c.key_configured && !!c.library_dir
}

export function scanSummary(r: JellyfinScanReport): string {
  const more = r.truncated ? ' (first part of a large library)' : ''
  return `${r.total} items: ${r.with_subtitles} have ${r.language.toUpperCase()} subtitles, ${r.missing} are missing them${more}.`
}

export function itemLabel(i: JellyfinScanItem): string {
  if (i.type === 'episode') {
    const s = i.season != null ? `S${String(i.season).padStart(2, '0')}` : ''
    const e = i.episode != null ? `E${String(i.episode).padStart(2, '0')}` : ''
    return [i.series, `${s}${e}`, i.name].filter(Boolean).join(' · ')
  }
  return i.name
}

export function sendResultText(r: JellyfinSendResult): string {
  const files = r.files.join(', ')
  const refresh = r.refresh === 'done'
    ? 'Jellyfin is rescanning the library.'
    : r.refresh === 'failed'
      ? "Couldn't ask Jellyfin to rescan; run a library scan in Jellyfin."
      : 'Rescan skipped.'
  return `Saved ${files}. ${refresh}`
}

// Server messages for these routes are fixed text (never the address, a
// path or the key), so a 409/422/503 message is safe to show.
export function jellyfinErrorMessage(err: unknown, keyWrite = false): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return keyWrite ? KEY_WRITES_REFUSED : 'Jellyfin is PC only. Run this on the main PC.'
    if (err.status === 0) return 'Could not reach the Baihe API. Is it running?'
    if ([404, 409, 422, 503].includes(err.status) && err.message) return err.message
  }
  return 'That did not work. Try again.'
}
