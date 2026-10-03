// Models for /api/data-usage (api/disk_usage_schemas.py). Every `path` is
// relative to the app's data folder and uses "/"; no model holds an absolute path.

export type DiskUsageRegenerable = { label: string; note: string }

export type DiskUsageMovable = {
  supported: boolean
  reason: string | null
  what: 'backups' | null
}

export type DiskUsageItem = {
  name: string
  path: string
  kind: 'file' | 'folder'
  size_bytes: number
  file_count: number
  percent_of_parent: number
  modified_at: string | null
  is_link: boolean
  complete: boolean
  protected: boolean
  protected_reason: string | null
  regenerable: DiskUsageRegenerable | null
  irreplaceable: boolean
  irreplaceable_note: string | null
  movable: DiskUsageMovable
}

export type DiskUsageScan = {
  path: string
  parent: string | null
  total_bytes: number
  file_count: number
  items: DiskUsageItem[]
  partial: boolean
  partial_reason: 'entries' | 'time' | null
  scanned_entries: number
  busy_reason: string | null
  recycle_available: boolean
  disk_total_bytes: number | null
  disk_free_bytes: number | null
}

export type DiskUsageClearDone = {
  freed_bytes: number
  file_count: number
  kind: 'file' | 'folder'
  name: string
}

export type DiskUsageMoveDone = {
  moved_bytes: number
  remaining_bytes: number
  what: string | null
  name: string
}
