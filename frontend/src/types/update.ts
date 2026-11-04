// App updates from the public GitHub Releases (api/schemas/app_update.py), from the generated
// schema. Every /api/system/update route is PC only; responses carry names, numbers and
// booleans, never a URL or a path.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type UpdateStatus = Schemas['UpdateStatus']
export type AndroidAppFetchResult = Schemas['AndroidAppFetchResult']
export type AppUpdateApplyResponse = Schemas['AppUpdateApplyResponse']
export type UpdateInstallResponse = Schemas['UpdateInstallResponse']

// Update and restart for a git working copy (api/routers/source_update_routes.py).
// `changes` and `reason` have server defaults, so the generated type makes them optional; the server always sends both.
export type SourceUpdateStatus = Omit<Schemas['SourceUpdateStatus'], 'changes' | 'reason'> & {
  changes: string[]
  reason: string | null
}
export type SourceUpdateApplyResponse = Schemas['SourceUpdateApplyResponse']
