// Mirrors api/notion_schemas.py (roadmap item 112: Notion export).

export type NotionTargetType = 'page' | 'database'

export interface NotionConfig {
  target_type: NotionTargetType | null
  target_id: string | null
  // The token itself is write-only and never comes back.
  token_configured: boolean
}

export interface NotionTestResult {
  ok: boolean
  bot_name: string
  target_title: string
  target_type: NotionTargetType
}

export interface NotionDramaPage {
  drama_id: number
  page_id: string | null
  page_url: string | null
}

export type NotionField = 'en' | 'zh' | 'bilingual'
