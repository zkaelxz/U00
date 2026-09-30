import type { Page, Route } from '@playwright/test'

// Stateful page.route mocks for /api/backups/* (automatic backups, Step 43)
// and the backup job's /api/jobs poll. Every write is recorded in `posts`;
// settings changes are kept, so a reload shows what was saved.

export interface SnapshotBody {
  exists: boolean
  readable?: boolean
  created_at?: string
  kind?: 'db-only' | 'full'
  size?: number
  app_version?: string
  drama_count?: number
}

export const SNAPSHOT: SnapshotBody = {
  exists: true, readable: true, created_at: '2026-09-28T09:30:00+00:00', kind: 'db-only',
  size: 12_345_678, app_version: '1.0', drama_count: 3,
}

export const SNAPSHOT_DRAMAS = [
  { id: 1, title: 'Grandmaster of Demonic Cultivation', media_type: 'audio_drama', line_count: 812, exists_now: true },
  { id: 2, title: "Heaven Official's Blessing", media_type: 'novel', line_count: 0, exists_now: false },
  { id: 3, title: 'Signal', media_type: 'video_drama', line_count: 1204, exists_now: false },
]

export function mockBackups(page: Page, opts: { snapshot?: SnapshotBody; jobPollsBeforeDone?: number } = {}) {
  const state = {
    settings: {
      enabled: false, frequency: 'weekly', include_media: false, folder: '', frequencies: ['daily', 'weekly', 'monthly'],
      last_run_at: null as string | null, last_attempt_at: null as string | null, last_error: null as string | null,
      next_run_at: null as string | null, running: false,
    },
    snapshot: opts.snapshot ?? SNAPSHOT,
    posts: [] as { path: string; body: Record<string, unknown> }[],
    jobStarted: false,
    polls: 0,
  }
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })

  const handler = async (route: Route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    if (req.method() === 'GET') {
      if (path === '/api/backups/settings') return json(route, state.settings)
      if (path === '/api/backups/snapshot') return json(route, state.snapshot)
      if (path === '/api/backups/snapshot/dramas') {
        return json(route, { created_at: state.snapshot.created_at, kind: state.snapshot.kind, dramas: SNAPSHOT_DRAMAS })
      }
      return route.abort()
    }
    const body = (req.postDataJSON() ?? {}) as Record<string, unknown>
    state.posts.push({ path, body })
    if (path === '/api/backups/settings') {
      if (typeof body.folder === 'string' && body.folder && !/^[A-Za-z]:\\/.test(body.folder)) {
        return json(route, { error: { code: 'invalid_input', message: 'The backup folder must be a full folder path.' } }, 422)
      }
      Object.assign(state.settings, body)
      state.settings.next_run_at = state.settings.enabled ? '2026-10-05T09:30:00+00:00' : null
      return json(route, state.settings)
    }
    if (path === '/api/backups/now') {
      if (state.snapshot.exists && body.replace !== true) {
        return json(route, { error: { code: 'invalid_input', message: 'A backup snapshot already exists; backing up now replaces it. Send replace=true to confirm.' } }, 422)
      }
      state.jobStarted = true
      return json(route, { job_id: 'library_auto_backup' })
    }
    if (path === '/api/backups/snapshot/restore-drama') {
      const d = SNAPSHOT_DRAMAS.find((x) => x.id === body.drama_id)
      if (!d) return json(route, { error: { code: 'not_found', message: "That drama isn't in the snapshot." } }, 404)
      return json(route, {
        drama_id: d.exists_now ? 40 : d.id, restored_as_new: d.exists_now,
        title: d.exists_now ? `${d.title} (restored 2026-09-30)` : d.title, media_restored: false,
        snapshot_kind: state.snapshot.kind, counts: { lines: d.line_count }, skipped_tables: ['bulk_jobs', 'usage_log'],
      })
    }
    if (path === '/api/backups/snapshot/delete') {
      state.snapshot = { exists: false }
      return json(route, { deleted: true })
    }
    return route.abort()
  }

  const job = (route: Route) => {
    if (!state.jobStarted) return json(route, { error: { code: 'not_found', message: 'No job.' } }, 404)
    state.polls += 1
    const done = state.polls > (opts.jobPollsBeforeDone ?? 1)
    if (done) {
      state.snapshot = { ...SNAPSHOT, created_at: '2026-09-30T08:00:00+00:00' }
      state.settings.last_run_at = '2026-09-30T08:00:00+00:00'
    }
    return json(route, {
      job_id: 'library_auto_backup', status: done ? 'done' : 'running', progress: done ? 1 : 0.4, message: '',
      error: null, description: 'Automatic backup', gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1,
      outcome: done ? 'ok' : null,
    })
  }

  return Promise.all([page.route('**/api/backups/**', handler), page.route('**/api/jobs/library_auto_backup', job)])
    .then(() => state)
}
