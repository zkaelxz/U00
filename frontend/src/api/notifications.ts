// Job notifications (api/routers/notification_routes.py). Set, clear and
// send test are PC only, so they go through pcOnlyFetch (a 403 marks the tab
// remote). The URL goes in the body only; responses never carry it back.
import type {
  NotificationChannel,
  NotificationChannelResult,
  NotificationStatus,
  NotificationTestResult,
} from '../types/notifications'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/settings/notifications'

export const getNotificationStatus = (f?: Fetch) => getJson<NotificationStatus>(BASE, f)

export const setNotificationChannel = (channel: NotificationChannel, value: string, f?: Fetch) =>
  postJson<NotificationChannelResult>(`${BASE}/${channel}`, { value, confirm: true }, pcOnlyFetch(f))

export const clearNotificationChannel = (channel: NotificationChannel, f?: Fetch) =>
  postJson<NotificationChannelResult>(`${BASE}/${channel}/clear`, { confirm: true }, pcOnlyFetch(f))

export const sendTestNotification = (f?: Fetch) =>
  postJson<NotificationTestResult>(`${BASE}/test`, {}, pcOnlyFetch(f))
