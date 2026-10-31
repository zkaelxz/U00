// /api/quick-video (api/schemas/quick_video.py)
export interface QuickVideoProfile {
  id: number
  name: string
  // The profile has the source language a new title needs.
  ready: boolean
}

export interface QuickVideoSettings {
  profile_id: number | null
  profiles: QuickVideoProfile[]
}

export interface QuickVideoStarted {
  drama_id: number
  watch_once: boolean
}

export type QuickVideoStep = 'download' | 'transcribe' | 'translate' | 'export'

export interface QuickVideoRun {
  drama_id: number
  title: string
  watch_once: boolean
  state: 'running' | 'stopped' | 'done'
  step: QuickVideoStep
  reason: string | null
}
