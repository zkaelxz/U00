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
  // Linked folders stored elsewhere that this item is or holds, kept out of size_bytes.
  // null: none was measured. Never names where they are.
  linked_bytes: number | null
  linked_files: number | null
  linked_complete: boolean | null
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
  // Plus this much in linked folders stored elsewhere: not part of total_bytes.
  linked_bytes: number
  linked_files: number
  linked_complete: boolean
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
  // null: the walk limit was reached before this item was measured.
  size_bytes: number | null
  file_count: number | null
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

// Voice clips in a title's voice_refs/ that no speaker uses. No file name or path: `id` is opaque.
export type UnusedVoiceClip = { id: string; file_type: string; size_bytes: number; modified_at: string | null }

export type UnusedVoiceClipTitle = { title: string; size_bytes: number; clips: UnusedVoiceClip[] }

export type UnusedVoiceClipList = {
  titles: UnusedVoiceClipTitle[]
  total_bytes: number
  total_count: number
  // Titles left out because a dub, narration or audiobook job is running for them.
  titles_in_use: number
  busy_reason: string | null
}

export type UnusedVoiceClipTrashDone = {
  moved_count: number
  moved_bytes: number
  skipped: { id: string; reason: 'no_longer_unused' | 'changed' }[]
}

// "Clean temp files now": counts and megabytes only, never a path.
export type TempCleanDone = { removed: number; freed_mb: number }
