// Pure text helpers for the Jellyfin connector (Settings card and the Export
// stage's "Send to Jellyfin"). Unit-tested.
import { ApiError } from '../../api/client'
import type { AssExportRequest } from '../../types/export'
import type {
  JellyfinConfig, JellyfinScanItem, JellyfinScanReport, JellyfinSendRequest, JellyfinSendResult,
} from '../../types/jellyfin'
import { couldNotReachBaihe, keyWritesRefused } from '../../components/errorMessages'

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

// The ASS style (or its error) is only asked for when the file is ASS, so a
// bad style field never blocks an SRT send.
export function buildSendBody(
  base: Omit<JellyfinSendRequest, 'ass'>,
  assRequest: () => { request: AssExportRequest; error?: undefined } | { request?: undefined; error: string },
): { body: JellyfinSendRequest; error?: undefined } | { body?: undefined; error: string } {
  if (base.format !== 'ass') return { body: base }
  const built = assRequest()
  if (built.error !== undefined) return { error: built.error }
  return { body: { ...base, ass: built.request } }
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
    if (err.status === 403) return keyWrite ? keyWritesRefused() : 'Jellyfin is PC only. Run this on the main PC.'
    if (err.status === 0) return couldNotReachBaihe()
    if ([404, 409, 422, 503].includes(err.status) && err.message) return err.message
  }
  return 'That did not work. Try again.'
}
