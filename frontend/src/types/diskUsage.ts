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
  contains_link: boolean
  complete: boolean
  protected: boolean
  protected_reason: string | null
  regenerable: DiskUsageRegenerable | null
  irreplaceable: boolean
  irreplaceable_note: string | null
  movable: DiskUsageMovable
}

export type DiskUsageTrashSummary = { size_bytes: number; item_count: number; partial: boolean }

export type DiskUsageScan = {
  path: string
  parent: string | null
  total_bytes: number
  file_count: number
  items: DiskUsageItem[]
  partial: boolean
  partial_reason: 'entries' | 'time' | 'items' | null
  not_shown: number
  scanned_entries: number
  busy_reason: string | null
  trash: DiskUsageTrashSummary
  disk_total_bytes: number | null
  disk_free_bytes: number | null
}

// Moved into Trash: nothing is freed until it is deleted from there.
export type DiskUsageClearDone = {
  moved_bytes: number
  file_count: number
  kind: 'file' | 'folder'
  name: string
  trash_id: string
}

export type DiskUsageTrashItem = {
  id: string
  original_path_relative: string | null
  kind: 'file' | 'folder' | null
  size_bytes: number
  file_count: number
  trashed_at: string | null
  restorable: boolean
}

export type DiskUsageTrashList = {
  items: DiskUsageTrashItem[]
  size_bytes: number
  item_count: number
  partial: boolean
  busy_reason: string | null
}

export type DiskUsageTrashRestoreDone = {
  name: string
  kind: 'file' | 'folder'
  size_bytes: number
  file_count: number
}

export type DiskUsageTrashPurgeDone = { freed_bytes: number; file_count: number }

export type DiskUsageTrashEmptyDone = { freed_bytes: number; removed: number; failed: number }

export type DiskUsageMoveDone = {
  moved_bytes: number
  remaining_bytes: number
  what: string | null
  name: string
}
