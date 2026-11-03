// Pure clock for the Follow along page: audio plays in another app, so this keeps its own time.

export interface FollowClock {
  running: boolean
  // Seconds into the episode at the moment of `anchor`; while paused it is the position itself.
  base: number
  // Wall-clock ms when `base` was taken; only read while running.
  anchor: number
}

export const STOPPED_AT_START: FollowClock = { running: false, base: 0, anchor: 0 }

export function positionAt(clock: FollowClock, nowMs: number): number {
  if (!clock.running) return clock.base
  return Math.max(0, clock.base + (nowMs - clock.anchor) / 1000)
}

export function start(clock: FollowClock, nowMs: number): FollowClock {
  return clock.running ? clock : { running: true, base: clock.base, anchor: nowMs }
}

export function pause(clock: FollowClock, nowMs: number): FollowClock {
  return { running: false, base: positionAt(clock, nowMs), anchor: 0 }
}

// Moves the position by `delta` seconds (negative goes back) without changing whether it runs.
// Used for both the jump buttons and the fine nudge, so a drift fix never stops the clock.
export function nudge(clock: FollowClock, delta: number, nowMs: number): FollowClock {
  const at = Math.max(0, positionAt(clock, nowMs) + delta)
  return { running: clock.running, base: at, anchor: nowMs }
}

export function seekTo(clock: FollowClock, seconds: number, nowMs: number): FollowClock {
  return { running: clock.running, base: Math.max(0, seconds), anchor: nowMs }
}

export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}
