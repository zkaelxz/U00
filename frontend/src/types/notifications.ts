// Discord / ntfy job notifications (api/routers/notification_routes.py).
// The API only ever returns booleans and outcome words, never a saved URL.
export type NotificationChannel = 'discord' | 'ntfy'

export type NotificationOutcome = 'sent' | 'failed' | 'refused' | 'not_configured'

export interface NotificationStatus {
  discord_configured: boolean
  ntfy_configured: boolean
  ntfy_allow_local: boolean
  // Which events go to Discord/ntfy (the header bell always lists every event).
  send_jobs: boolean
  send_chapters: boolean
  send_remote: boolean
}

// POST /api/settings/notifications/categories: an omitted field stays as it is.
export interface NotificationCategories {
  jobs?: boolean
  chapters?: boolean
  remote?: boolean
}

export interface NotificationChannelResult {
  channel: NotificationChannel
  configured: boolean
}

export interface NotificationTestResult {
  results: Record<NotificationChannel, NotificationOutcome>
}

// GET /api/notifications: the header bell's list (in memory on the server,
// lost on restart). Newest first, at most 50. No job id, link or path.
export type NotificationKind = 'job_done' | 'job_failed' | 'chapters' | 'remote'

export interface NotificationItem {
  id: number
  /** Unix seconds. */
  at: number
  kind: NotificationKind
  text: string
}

export interface NotificationList {
  items: NotificationItem[]
}
