// Job notifications (api/routers/notification_routes.py). All three writes
// are PC only. Send test goes through pcOnlyFetch (a 403 marks the tab
// remote). Set and clear also sit behind the key-write gate, whose 403 on
// the PC itself just means key writes are off, so, like setEngineKey, they
// use a plain fetch and show WRITES_REFUSED instead of hiding PC-only
// sections. The URL goes in the body only; responses never carry it back.
// The category switches (which events are pushed) are PC only but not behind
// the key-write gate, so they go through pcOnlyFetch like Send test.
// The header bell's list (GET /api/notifications) is library.read.
import type {
  NotificationCategories,
  NotificationChannel,
  NotificationChannelResult,
  NotificationList,
  NotificationStatus,
  NotificationTestResult,
} from '../types/notifications'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/settings/notifications'

export const getNotificationStatus = (f?: Fetch) => getJson<NotificationStatus>(BASE, f)

export const setNotificationChannel = (channel: NotificationChannel, value: string, f?: Fetch) =>
  postJson<NotificationChannelResult>(`${BASE}/${channel}`, { value, confirm: true }, f)

export const clearNotificationChannel = (channel: NotificationChannel, f?: Fetch) =>
  postJson<NotificationChannelResult>(`${BASE}/${channel}/clear`, { confirm: true }, f)

export const sendTestNotification = (f?: Fetch) =>
  postJson<NotificationTestResult>(`${BASE}/test`, {}, pcOnlyFetch(f))

/** Send only the switches that changed; the reply is the full status. */
export const setNotificationCategories = (changes: NotificationCategories, f?: Fetch) =>
  postJson<NotificationStatus>(`${BASE}/categories`, changes, pcOnlyFetch(f))

export const listNotifications = (f?: Fetch) => getJson<NotificationList>('/api/notifications', f)
