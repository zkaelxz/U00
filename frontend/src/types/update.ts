// Mirrors api/schemas.py UpdateStatus / UpdateInstallResponse (app updates
// from the public GitHub Releases). Every /api/system/update route is PC
// only; responses carry names, numbers and booleans, never a URL or a path.

export type UpdateDownloadState = 'idle' | 'downloading' | 'verified' | 'failed'

export interface UpdateStatus {
  // null for a source checkout (it updates with git).
  current: string | null
  installed: boolean
  latest: string | null
  update_available: boolean
  notes: string
  installer_name: string | null
  size: number | null
  // Unix seconds.
  checked_at: number | null
  check_error: string | null
  // not_found: GitHub answered 404 (repository missing, renamed or private).
  release_lookup: 'unchecked' | 'found' | 'no_installer_release' | 'not_found'
  download: UpdateDownloadState
  downloaded_bytes: number
  download_error: string | null
  verified: boolean
  // The version and file Install would start (the verified download).
  verified_version: string | null
  verified_name: string | null
  can_install: boolean
  auto_check: boolean
  // BAIHE_UPDATE_REPO names another repository than the default.
  custom_source: boolean
}

export interface UpdateInstallResponse {
  launched: boolean
  installer_name: string
  version: string
}
