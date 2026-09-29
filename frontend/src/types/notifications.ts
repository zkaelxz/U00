// Step 44: Discord / ntfy job notifications (api/routers/notification_routes.py).
// The API only ever returns booleans and outcome words, never a saved URL.
export type NotificationChannel = 'discord' | 'ntfy'

export type NotificationOutcome = 'sent' | 'failed' | 'refused' | 'not_configured'

export interface NotificationStatus {
  discord_configured: boolean
  ntfy_configured: boolean
  ntfy_allow_local: boolean
}

export interface NotificationChannelResult {
  channel: NotificationChannel
  configured: boolean
}

export interface NotificationTestResult {
  results: Record<NotificationChannel, NotificationOutcome>
}
