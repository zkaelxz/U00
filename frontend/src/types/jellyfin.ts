// api/jellyfin_schemas.py (optional Jellyfin connector); responses come from the generated schema.
import type { components } from '../api/generated/schema'
import type { AssExportRequest } from './export'

type Schemas = components['schemas']

export type JellyfinConfig = Schemas['JellyfinConfig']
export type JellyfinScanItem = Schemas['JellyfinScanItem']
export type JellyfinScanReport = Schemas['JellyfinScanReport']

export type JellyfinLanguage = 'en' | 'zh' | 'ja' | 'ko'

export interface JellyfinSendRequest {
  item_id?: string
  format: 'srt' | 'ass'
  field: 'en' | 'zh' | 'bilingual'
  language?: JellyfinLanguage
  media: 'none' | 'source' | 'dubbed'
  overwrite: boolean
  refresh: boolean
  ass?: AssExportRequest
}

export type JellyfinSendResult = Schemas['JellyfinSendResult']
