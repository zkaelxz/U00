// Who can see each drama and series (api/sharing_schemas.py).

export type SharingKind = 'series' | 'drama'

export type SharingItem = {
  kind: SharingKind
  id: number
  title: string
  owner_name: string
  is_private: boolean
  // Dramas only: a drama in a series follows the series' flag.
  series_id: number | null
  series_name: string | null
  series_is_private: boolean | null
}

export type SharingList = { total: number; offset: number; limit: number; items: SharingItem[] }

export type SetPrivateResult = { kind: SharingKind; id: number; is_private: boolean }

export type ShareByDefault = { share_by_default: boolean }
