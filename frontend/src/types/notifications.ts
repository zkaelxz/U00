// Discord / ntfy job notifications (api/routers/notification_routes.py), from the generated schema.
// The API only ever returns booleans and outcome words, never a saved URL.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type NotificationChannel = 'discord' | 'ntfy'

export type NotificationOutcome = 'sent' | 'failed' | 'refused' | 'not_configured'

// Which events go to Discord/ntfy (the header bell always lists every event).
export type NotificationStatus = Schemas['NotificationSettingsStatus']

// POST /api/settings/notifications/categories: an omitted field stays as it is.
export type NotificationCategories = Schemas['NotificationCategoriesRequest']

export type NotificationChannelResult = Schemas['NotificationChannelResult']

// Keyed by channel, which the generated type leaves as any string.
export interface NotificationTestResult {
  results: Record<NotificationChannel, NotificationOutcome>
}

// GET /api/notifications: the header bell's list (in memory on the server,
// lost on restart). Newest first, at most 50. No job id, link or path.
export type NotificationKind = 'job_done' | 'job_failed' | 'chapters' | 'remote' | 'live'

export type NotificationItem = Schemas['NotificationEvent']

export type NotificationList = Schemas['NotificationList']
