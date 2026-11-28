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
import { couldNotReachBaihe, keyWritesRefused } from '../../components/errorMessages'

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
export type CategoryField = 'send_jobs' | 'send_chapters' | 'send_remote' | 'show_private_titles'

export const CATEGORIES: { field: CategoryField; body: keyof NotificationCategories; label: string; help: string }[] = [
  {
    field: 'send_jobs',
    body: 'jobs',
    label: 'Jobs finished, failed or waiting for you',
    help: 'A message when a job (transcribe, translate, dub, export and so on) finishes or fails, and when a run stops and waits for you.',
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
  {
    field: 'show_private_titles',
    body: 'private_titles',
    label: 'This channel is only mine: show private titles',
    help: 'Off: a title or series that not everyone in the household can see is left out of the message. Turn on only if you alone can read this channel.',
  },
]

export const PC_NAME_HELP =
  'Shown on every Discord message so you can tell your PCs apart, for example V100 or 3080. Leave it empty to use the Windows computer name.'

export const CATEGORIES_NOTE =
  'These choose what goes to Discord and ntfy. PC alerts, and your followed channels going live, are always sent; a job that finishes cleanly in under two minutes is not. The Alerts button at the top of the page always lists every event.'

/** The request body for one switch: only the field that changed. */
export function categoryChange(field: CategoryField, next: boolean): NotificationCategories {
  const c = CATEGORIES.find((x) => x.field === field)!
  return { [c.body]: next }
}

export function isConfigured(status: NotificationStatus, channel: NotificationChannel): boolean {
  return channel === 'discord' ? status.discord_configured : status.ntfy_configured
}

export const NO_DESKTOP_POPUPS =
  'Baihe is running as the Windows boot service, which has no desktop, so pop-ups can’t show. Discord and ntfy still work.'

// The folded card and the open card show this same line, so it counts the PC pop-up as well as the channels.
export function notificationSummary(status: NotificationStatus | null, popupOn = false): string {
  const on = [
    ...(popupOn ? ['Pop-up on this PC'] : []),
    ...(status ? CHANNELS.filter((c) => isConfigured(status, c.channel)).map((c) => c.label) : []),
  ]
  return on.length ? `On: ${on.join(', ')}` : 'Off'
}

// One plain line. A 422 message from the server is a fixed string that never
// echoes the address, so it is safe to show.
export function notificationErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return keyWritesRefused()
    if (err.status === 0) return couldNotReachBaihe()
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
