import { getJson, type Fetch } from './client'

export interface ActivityItem {
  id: number
  label: string
  kind: string
  outcome: 'done' | 'error' | 'cancelled'
  started_at: number
  ended_at: number
  seconds: number
  gpu: boolean
  mine: boolean
}
export interface ActivityList {
  items: ActivityItem[]
  next_before_id: number | null
}
export interface GpuDay {
  day: string
  gpu_seconds: number
  jobs: number
}
export interface GpuDaily {
  days: GpuDay[]
  total_gpu_seconds: number
}

// Finished jobs the caller may see, newest first; `beforeId` continues a page.
export const getActivity = (beforeId?: number | null, f?: Fetch) =>
  getJson<ActivityList>(`/api/activity?limit=25${beforeId ? `&before_id=${beforeId}` : ''}`, f)
export const getGpuDaily = (days = 30, f?: Fetch) => getJson<GpuDaily>(`/api/activity/gpu-daily?days=${days}`, f)
