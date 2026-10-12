export interface SourceSuspectCheckResult {
  flagged_count: number
  cleared_count: number
}

export interface SourceSuspectItem {
  line_id: number
  idx: number
  zh: string
  note: string
  next_line_id: number | null
  next_zh: string
}

export interface SourceSuspectList {
  count: number
  items: SourceSuspectItem[]
}
