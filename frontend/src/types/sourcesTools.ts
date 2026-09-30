// Sources tools (feature inventory SO02, SO03, SO08, SO16) and the Discover
// pasted listing (DI07). Mirrors api/sources_tools_schemas.py and the job
// results in services/sources_tools_service.py. URLs are scheme+host+path;
// server text is scrubbed.
import type { UrlContentType, UrlPreview } from './sourcesImport'

// SO02 result (job sources_url_preflight).
export interface UrlPreflight {
  kind: 'url_preflight'
  ok: boolean
  verdict: string
  permitted: boolean
  reachable: boolean
  content_type: UrlContentType | string
  tier: string
  adapter: string | null
  title: string
  text_chars: number
  images: number
  confidence: string
  // Whether the page has a next-/previous-chapter link (a next link can be followed).
  next_link?: boolean
  previous_link?: boolean
  warnings: string[]
  lines: string[]
  display_url: string
}

// SO03: the URL preview read from pasted page source (synchronous).
export interface PastedPreview extends UrlPreview {
  pasted: true
}

// SO08 result (job sources_url_identify).
export interface MediaResource {
  index: number
  kind: string
  role: string
  language: string | null
  label: string | null
  display_url: string
  downloadable: boolean
}

export interface MediaIdentify {
  kind: 'media_identify'
  run_id: string
  found: boolean
  needs_review: boolean
  reason: string
  protection: string[]
  resources: MediaResource[]
}

// PC only: one pick's full address.
export interface MediaResourceUrl {
  run_id: string
  index: number
  resource_url: string
}

// SO16 row.
export interface SourceExtraction {
  url: string
  created_at: number | null
  content_type: string
  headline: string
  tier: string | null
  extraction_tier: string | null
  llm_calls: number
  cache_hit: boolean
  profile: string
  confidence: string | null
  access: {
    authentication: string | null
    entitlement: string | null
    technical_protection: string | null
    protection_detail: string[]
  } | null
  resource_types: string[]
  reason: string
  lines: string[]
}
