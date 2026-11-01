// Models for /api/data-usage, taken from the generated schema (api/disk_usage_schemas.py).
// Every `path` is relative to the app's data folder and uses "/"; no model holds an absolute path.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type DiskUsageTitleSizes = Schemas['DiskUsageTitleSizes']
export type DiskUsageItem = Schemas['DiskUsageItem']
export type DiskUsageScan = Schemas['DiskUsageScan']
export type DiskUsageClearDone = Schemas['DiskUsageClearDone']
export type DiskUsageTrashItem = Schemas['DiskUsageTrashItem']
export type DiskUsageTrashList = Schemas['DiskUsageTrashList']
export type DiskUsageTrashRestoreDone = Schemas['DiskUsageTrashRestoreDone']
export type DiskUsageTrashPurgeDone = Schemas['DiskUsageTrashPurgeDone']
export type DiskUsageTrashEmptyDone = Schemas['DiskUsageTrashEmptyDone']
export type DiskUsageMoveDone = Schemas['DiskUsageMoveDone']
export type UnusedVoiceClip = Schemas['UnusedVoiceClip']
export type UnusedVoiceClipList = Schemas['UnusedVoiceClipList']
export type UnusedVoiceClipTrashDone = Schemas['UnusedVoiceClipTrashDone']
export type TempCleanDone = Schemas['TempCleanResult']
export type PreviousLibrary = Schemas['PreviousLibrary']
export type PreviousLibraryDeleted = Schemas['PreviousLibraryDeleted']
