// Pure text helpers for Settings > Notifications (unit-tested). Nothing here
// ever sees a saved URL: the API returns booleans and outcome words only.
import { ApiError } from '../../api/client'
import type {
  NotificationCategories,
  NotificationChannel,
  NotificationOutcome,
  NotificationStatus,
  NotificationTestResult,
} from '../../types/notifications'
import { KEY_WRITES_REFUSED } from '../../components/errorMessages'

export const CHANNELS: {
  channel: NotificationChannel
  label: string
  field: string
  help: string
  placeholder: string
}[] = [
  {
    channel: 'discord',
    label: 'Discord',
    field: 'Discord webhook address',
    help: 'In Discord: Server Settings, Integrations, Webhooks, Copy Webhook URL.',
    placeholder: 'https://discord.com/api/webhooks/…',
  },
  {
    channel: 'ntfy',
    label: 'ntfy',
    field: 'ntfy topic address',
    help: 'For example https://ntfy.sh/a-long-private-topic-name. Anyone who knows the topic can read it.',
    placeholder: 'https://ntfy.sh/your-topic',
  },
]

// "What to send": which events go to Discord/ntfy. `field` is the status
// field, `body` the key POST .../categories takes.
export type CategoryField = 'send_jobs' | 'send_chapters' | 'send_remote'

export const CATEGORIES: { field: CategoryField; body: keyof NotificationCategories; label: string; help: string }[] = [
  {
    field: 'send_jobs',
    body: 'jobs',
    label: 'Jobs finished or failed',
    help: 'A message when a job (transcribe, translate, dub, export and so on) finishes or fails.',
  },
  {
    field: 'send_chapters',
    body: 'chapters',
    label: 'New chapters found',
    help: 'A message when tracked sources have new chapters.',
  },
  {
    field: 'send_remote',
    body: 'remote',
    label: 'Remote access problems',
    help: 'A message when remote access stops working or its certificate is not renewed in time, and again when it is fixed.',
  },
]

export const CATEGORIES_NOTE =
  'These choose what goes to Discord and ntfy. The bell at the top of the page always lists every event.'

/** The request body for one switch: only the field that changed. */
export function categoryChange(field: CategoryField, next: boolean): NotificationCategories {
  const c = CATEGORIES.find((x) => x.field === field)!
  return { [c.body]: next }
}

export function isConfigured(status: NotificationStatus, channel: NotificationChannel): boolean {
  return channel === 'discord' ? status.discord_configured : status.ntfy_configured
}

export function notificationSummary(status: NotificationStatus): string {
  const on = CHANNELS.filter((c) => isConfigured(status, c.channel)).map((c) => c.label)
  return on.length ? `On: ${on.join(', ')}` : 'Off'
}

// One plain line. A 422 message from the server is a fixed string that never
// echoes the address, so it is safe to show.
export function notificationErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return KEY_WRITES_REFUSED
    if (err.status === 0) return 'Could not reach the Baihe API. Is it running?'
    if (err.status === 429) return 'Too many notifications in the last minute. Wait a moment and try again.'
    if (err.status === 422 && err.message) return err.message
  }
  return 'That did not work. Try again.'
}

const OUTCOME_TEXT: Record<NotificationOutcome, string> = {
  sent: 'sent',
  failed: 'could not be sent (check the address and your connection)',
  refused: 'refused, the address is not allowed',
  not_configured: 'not set up',
}

export function testResultText(r: NotificationTestResult): string {
  return CHANNELS.filter((c) => r.results[c.channel] && r.results[c.channel] !== 'not_configured')
    .map((c) => `${c.label}: ${OUTCOME_TEXT[r.results[c.channel]]}.`)
    .join(' ')
}
