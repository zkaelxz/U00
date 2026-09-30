// Mirrors api/jellyfin_schemas.py (roadmap Step 39: optional Jellyfin connector).

export interface JellyfinConfig {
  enabled: boolean
  server_url: string | null
  library_dir: string | null
  key_configured: boolean
}

export interface JellyfinScanItem {
  id: string
  name: string
  series: string | null
  season: number | null
  episode: number | null
  type: 'movie' | 'episode'
  writable: boolean
}

export interface JellyfinScanReport {
  language: string
  total: number
  with_subtitles: number
  missing: number
  items: JellyfinScanItem[]
  truncated: boolean
}

export type JellyfinLanguage = 'en' | 'zh' | 'ja' | 'ko'

export interface JellyfinSendRequest {
  item_id?: string
  format: 'srt' | 'ass'
  field: 'en' | 'zh' | 'bilingual'
  language?: JellyfinLanguage
  media: 'none' | 'source' | 'dubbed'
  overwrite: boolean
  refresh: boolean
}

export interface JellyfinSendResult {
  drama_id: number
  files: string[]
  refresh: 'done' | 'failed' | 'skipped'
}
