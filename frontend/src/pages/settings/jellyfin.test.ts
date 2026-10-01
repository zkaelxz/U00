import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { JellyfinConfig, JellyfinScanItem } from '../../types/jellyfin'
import {
  itemLabel, jellyfinErrorMessage, jellyfinSummary, readyToSend, scanSummary, sendResultText,
} from './jellyfin'
import { KEY_WRITES_REFUSED } from '../../components/errorMessages'

const cfg: JellyfinConfig = { enabled: true, server_url: 'http://x:8096', library_dir: 'D:\\M', key_configured: true }

describe('jellyfin helpers', () => {
  it('summarizes the setup', () => {
    expect(jellyfinSummary({ ...cfg, enabled: false })).toBe('Off')
    expect(jellyfinSummary({ ...cfg, key_configured: false })).toBe('On, not set up')
    expect(jellyfinSummary({ ...cfg, library_dir: null })).toBe('On, no library folder')
    expect(jellyfinSummary(cfg)).toBe('On')
    expect(readyToSend(cfg)).toBe(true)
    expect(readyToSend({ ...cfg, library_dir: null })).toBe(false)
    expect(readyToSend(null)).toBe(false)
  })

  it('labels scan results and items', () => {
    expect(scanSummary({ language: 'en', total: 3, with_subtitles: 1, missing: 2, items: [], truncated: false }))
      .toBe('3 items: 1 have EN subtitles, 2 are missing them.')
    const ep: JellyfinScanItem = { id: '1', name: 'Pilot', series: 'Show', season: 1, episode: 2, type: 'episode', writable: true }
    expect(itemLabel(ep)).toBe('Show · S01E02 · Pilot')
    expect(itemLabel({ ...ep, type: 'movie', name: 'Film' })).toBe('Film')
  })

  it('reports a send and errors', () => {
    expect(sendResultText({ drama_id: 1, files: ['Show/S01E01.eng.srt'], refresh: 'done' }))
      .toBe('Saved Show/S01E01.eng.srt. Jellyfin is rescanning the library.')
    expect(sendResultText({ drama_id: 1, files: ['a'], refresh: 'failed' })).toMatch(/run a library scan/)
    expect(jellyfinErrorMessage(new ApiError(403, { code: 'forbidden', message: 'x' }), true)).toBe(KEY_WRITES_REFUSED)
    expect(jellyfinErrorMessage(new ApiError(409, { code: 'conflict', message: 'a.srt already exists' }))).toBe('a.srt already exists')
    expect(jellyfinErrorMessage(new Error('boom'))).toBe('That did not work. Try again.')
  })
})
