// /api/vdb-names (api/schemas/vdb_names.py). Counts and booleans only.

export interface VdbNamesStatus {
  downloaded: boolean
  entry_count: number
  name_count: number
  age_days: number | null
  newer_than_30_days: boolean
}

export interface TitleVdbNames {
  enabled: boolean
  downloaded: boolean
}
