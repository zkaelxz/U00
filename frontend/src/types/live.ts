// Hand-written mirrors of api/schemas/transcribe.py (Live* models).
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type LiveSessionStart = Schemas['LiveSessionStart']

/** GET /api/live/options: what the form offers and starts on (the values Start checks). */
export type LiveFormOptions = Schemas['LiveFormOptions']

export type LiveOllamaCheck = Schemas['LiveOllamaCheck']

export type LiveSessionStarted = Schemas['LiveSessionStarted']

export type LiveCue = Schemas['LiveCue']

export type LiveSessionStatus = Schemas['LiveSessionStatus']

export type LiveSessionSummary = Schemas['LiveSessionSummary']

export type LiveSessionStopped = Schemas['LiveSessionStopped']

// Followed channels (api/routers/live_follow_routes.py). Never a URL:
// `channel` is the short label ("@name", "channel/UC...").
export type LiveFollowMode = 'notify' | 'auto'
export type LiveFollowOptions = Schemas['LiveFollowOptions']

export type LiveFollow = Schemas['LiveFollow']

export type LiveFollowList = Schemas['LiveFollowList']

export type LiveFollowAdd = Schemas['LiveFollowAdd']

export type LiveFollowUpdate = Schemas['LiveFollowUpdate']

export type LiveChatMessage = Schemas['LiveChatMessage']

/** GET /api/live/sessions/{id}/chat?after=: messages with an id above `after`. */
export type LiveChatPage = Schemas['LiveChatPage']
